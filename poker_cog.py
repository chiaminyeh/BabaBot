
import discord
import os
import logging
import random
import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace
from discord import app_commands
from discord.ext import commands, tasks
from poker_ai_strategy import RLCardRuleStrategy

# --- Player Class ---
class Player:
    def __init__(
        self,
        user: discord.User,
        *,
        is_bot: bool = False,
        seat_index: int | None = None,
    ):
        self.user = user
        self.id = user.id
        self.name = user.name
        self.hand: list[str] = []
        self.bet: int = 0
        self.folded: bool = False
        self.all_in: bool = False
        self.is_bot = is_bot
        self.seat_index = seat_index
        self.position = ""
        

# --- Poker Cog ---
class PokerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.baba = bot.baba
        # bank: mapping user_id -> (balance:int, claimed:bool)
        self.bank = bot.baba.bank
        self.money_name = bot.baba.money_name
        self.games: dict[int, dict] = {}
        self.minimum_bet = 10
        self.game_timeout = 300  # seconds inactivity
        self.setup_logging()
        self.timeout_check.start()

    def setup_logging(self):
        os.makedirs('logs', exist_ok=True)
        logging.basicConfig(
            filename=f'logs/poker_{datetime.now():%Y%m%d}.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

    @staticmethod
    def build_bot_player(guild_id: int, index: int) -> Player:
        """Create a deterministic local bot seat for a poker hand."""
        if not isinstance(guild_id, int) or not isinstance(index, int):
            raise TypeError("guild_id and index must be integers")
        user = SimpleNamespace(
            id=-(abs(guild_id) * 100 + index + 1),
            name=f"BabaBot {index + 1}",
        )
        return Player(user, is_bot=True)

    def ensure_bot_bankroll(self, player: Player):
        """Give a bot a bounded house bankroll without touching raw bank state."""
        target = max(100, self.minimum_bet * 20)
        balance = self.baba.get_money(player.id)
        if balance < target:
            self.baba.add_money(player.id, target - balance)

    # --- Slash: Start Poker ---
    @app_commands.command(name='start_poker')
    @app_commands.describe(
        min_players="Total seats, including bots (2–10)",
        bot_count="Number of BabaBot opponents (0–9)",
    )
    async def start_poker(
        self,
        interaction: discord.Interaction,
        min_players: app_commands.Range[int, 2, 10] = 4,
        bot_count: app_commands.Range[int, 0, 9] = 0,
    ):
        
        """Start a new poker game, waiting for `min_players` to join."""
        guild_id = interaction.guild_id
        if bot_count >= min_players:
            return await interaction.response.send_message(
                "At least one human seat is required.", ephemeral=True
            )
        if self.baba.get_money(interaction.user.id) < self.minimum_bet:
            return await interaction.response.send_message(
                f"You need at least {self.minimum_bet} {self.money_name} to join.",
                ephemeral=True,
            )
        if guild_id in self.games and self.games[guild_id]['active']:
            return await interaction.response.send_message(
                "A game is already in progress!", ephemeral=True
            )

        # initialize game state with dynamic player count
        self.games[guild_id] = {
            'active': True,
            'players': [Player(interaction.user)],  # host joins their own table
            'channel_id': interaction.channel_id,
            'instance': None,         # PokerGame instance
            'last_action': datetime.now(),
            'min_players': min_players,
            'bot_count': bot_count,
            'human_target': min_players - bot_count,
            'starting': False,
        }

        embed = discord.Embed(
            title="Poker Game",
            description=(
                f"Seats: **1/{min_players}** (host joined)\n"
                f"Humans: **1/{min_players - bot_count}**\n"
                f"BabaBot opponents: **{bot_count}**\n"
                f"Minimum bet: {self.minimum_bet} {self.money_name}\n"
                "Click below to join!"
            ), color=discord.Color.dark_green()
        )
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()

        # Join button
        view = discord.ui.View(timeout=None)
        btn = discord.ui.Button(label="Join Game", style=discord.ButtonStyle.primary)

        async def btn_cb(btn_inter: discord.Interaction):
            user = btn_inter.user
            game = self.games[guild_id]

            if not game['active']:
                return await btn_inter.response.send_message("This lobby has expired.", ephemeral=True)
            if self.baba.get_money(user.id) < self.minimum_bet:
                return await btn_inter.response.send_message(
                    f"You need at least {self.minimum_bet} {self.money_name} to join.", ephemeral=True
                )
            if any(p.id == user.id for p in game['players']):
                return await btn_inter.response.send_message("Already joined!", ephemeral=True)
            human_count = len([p for p in game['players'] if not p.is_bot])
            if human_count >= game['human_target']:
                return await btn_inter.response.send_message(
                    "All human seats are already occupied.", ephemeral=True
                )

            game['players'].append(Player(user))
            game['last_action'] = datetime.now()
            human_count += 1
            await btn_inter.response.send_message(
                f"{user.name} joined! ({human_count}/{game['human_target']} humans)"
            )

            if human_count >= game['human_target']:
                self._add_bots_to_lobby(guild_id)
                await self._begin_game(guild_id)

        btn.callback = btn_cb
        view.add_item(btn)
        await msg.edit(view=view)

        # wait up to 60s for enough joins
        start = datetime.now()
        while (datetime.now() - start).seconds < 60:
            await asyncio.sleep(1)
            game = self.games[guild_id]
            if len([p for p in game['players'] if not p.is_bot]) >= game['human_target']:
                self._add_bots_to_lobby(guild_id)
                return await self._begin_game(guild_id)

        # timeout: not enough players
        self.games[guild_id]['active'] = False
        btn.disabled = True
        await msg.edit(
            content=f"Timed out after 60s — only {len(self.games[guild_id]['players'])}/{min_players} joined.",
            view=view,
            embed=None
        )

    async def _begin_game(self, guild_id: int):
        game = self.games[guild_id]
        if game['instance'] or game.get('starting'):
            return
        game['starting'] = True
        for player in game['players']:
            if player.is_bot:
                self.ensure_bot_bankroll(player)
        channel = self.bot.get_channel(game['channel_id'])
        players = game['players']
        try:
            poker = PokerGame(channel, players, self)
            game['instance'] = poker
            game['last_action'] = datetime.now()
            await poker.play_game()
        finally:
            game['starting'] = False

    def _add_bots_to_lobby(self, guild_id: int):
        game = self.games[guild_id]
        existing = len([p for p in game['players'] if p.is_bot])
        for index in range(existing, game['bot_count']):
            game['players'].append(self.build_bot_player(guild_id, index))

    # --- Slash: End Poker ---
    def _refund_bets(self, inst):
        return inst.refund_game("cog") if inst else False

    @app_commands.command(name='end_poker')
    @commands.has_permissions(administrator=True)
    async def end_poker(self, interaction: discord.Interaction):
        """Force-end the poker game and refund bets."""
        guild_id = interaction.guild_id
        game = self.games.get(guild_id)
        if not game or not game['active']:
            return await interaction.response.send_message("No active game.")
        # Refund the durable escrow before changing the lobby state or
        # confirming success to the administrator.
        refunded = await self._refund_bets(game['instance'])
        if not refunded:
            return await interaction.response.send_message(
                "Game could not be ended safely; the escrow remains active."
            )
        game['active'] = False
        game['instance'] = None
        await interaction.response.send_message("Game force-ended. Bets returned.")
    
    @app_commands.command(name='poker_rules')
    async def poker_rules(self, interaction: discord.Interaction):
        """Display the rules and hand rankings of poker"""
        embed = discord.Embed(title="Poker Rules", color=discord.Color.blue())
        embed.add_field(name="Basic Rules", value=
            "1. Each player is dealt 2 cards\n"
            "2. Players bet in rounds\n"
            "3. 5 community cards are revealed gradually\n"
            "4. Best 5-card hand wins", inline=False)
        
        embed.add_field(name="Hand Rankings (Highest to Lowest)", value=
            "1. Royal Flush\n"
            "2. Straight Flush\n"
            "3. Four of a Kind\n"
            "4. Full House\n"
            "5. Flush\n"
            "6. Straight\n"
            "7. Three of a Kind\n"
            "8. Two Pair\n"
            "9. One Pair\n"
            "10. High Card", inline=False)
        
        embed.add_field(name="Commands", value=
            "!bet [amount] - Place a bet\n"
            "!call - Match the current bet\n"
            "!fold - Forfeit your hand\n"
            "!check - Pass when no bet is required", inline=False)
        
        await interaction.response.send_message(embed=embed)


    # --- Commands: bet, call, fold, allin ---
    @commands.command(name='bet')
    async def bet(self, ctx: commands.Context, amount: int):
        guild_id = ctx.guild.id
        game = self.games.get(guild_id)
        if not game or not game['active']:
            return await ctx.send("No game running.")
        poker: PokerGame = game['instance']
        await poker.place_bet(ctx, amount)
        game['last_action'] = datetime.now()

    @commands.command(name='call')
    async def call(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        game = self.games.get(guild_id)
        if not game or not game['active']:
            return await ctx.send("No game running.")
        poker: PokerGame = game['instance']
        await poker.call(ctx)
        game['last_action'] = datetime.now()

    @commands.command(name='check')
    async def check(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        game = self.games.get(guild_id)
        if not game or not game['active']:
            return await ctx.send("No game running.")
        poker: PokerGame = game['instance']

        # in PokerGame.check(), we’ll verify they can only check if
        # their bet equals the current highest.
        await poker.check(ctx)
        game['last_action'] = datetime.now()


    @commands.command(name='fold')
    async def fold(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        game = self.games.get(guild_id)
        if not game or not game['active']:
            return await ctx.send("No game running.")
        poker: PokerGame = game['instance']
        await poker.fold(ctx)
        game['last_action'] = datetime.now()

    @commands.command(name='allin')
    async def allin(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        game = self.games.get(guild_id)
        if not game or not game['active']:
            return await ctx.send("No game running.")
        poker: PokerGame = game['instance']
        await poker.allin(ctx)
        game['last_action'] = datetime.now()

    # --- Timeout Checker ---
    @tasks.loop(seconds=30)
    async def timeout_check(self):
        for guild_id, game in list(self.games.items()):
            if game['active'] and (datetime.now() - game['last_action']).seconds > self.game_timeout:
                # Complete the durable refund before changing state or
                # sending a success announcement.
                refunded = await self._refund_bets(game['instance'])
                if not refunded:
                    continue
                game['active'] = False
                game['instance'] = None
                chan = self.bot.get_channel(game['channel_id'])
                await chan.send("Game ended due to inactivity.")

# --- Deck Class ---
class Deck:
    def __init__(self):
        self.suits = ['♠','♣','♥','♦']
        self.ranks = ['A','2','3','4','5','6','7','8','9','10','J','Q','K']
        self.cards = [r + s for s in self.suits for r in self.ranks]
        random.shuffle(self.cards)
    def deal(self, n: int) -> list[str]:
        cards, self.cards = self.cards[:n], self.cards[n:]
        return cards
    def value(self, card: str) -> int:
        r = card[:-1]
        face = {'A':14, 'K':13, 'Q':12, 'J':11}
        if r in face:
            return face[r]
        else:
            return int(r)
    def suit(self, card: str) -> str:
        return card[-1]

# --- PokerGame Class ---
class PokerGame:
    def __init__(
        self,
        channel: discord.TextChannel,
        players: list[Player],
        cog: PokerCog,
        *,
        hand_id: str | None = None,
        button_seat: int = 0,
        small_blind: int | None = None,
        big_blind: int | None = None,
    ):
        self.channel = channel
        self.players = players
        self.cog = cog
        self.deck = Deck()
        self.community: list[str] = []
        self.pot: int = 0
        self.big_blind = big_blind or cog.minimum_bet
        self.small_blind = small_blind or max(1, self.big_blind // 2)
        if self.small_blind <= 0 or self.big_blind <= 0:
            raise ValueError("blinds must be positive")
        if self.small_blind > self.big_blind:
            raise ValueError("small blind cannot exceed big blind")
        self.bot_strategy = RLCardRuleStrategy(big_blind=self.big_blind)
        self.highest: int = self.big_blind
        self.round = 0
        self.street = "preflop"
        self.acted_players: set[int] = set()
        self.current_actor_id: int | None = None
        self.button_seat = button_seat % len(players) if players else 0
        self._assign_seats_and_positions()
        self.hand_id = hand_id or uuid.uuid4().hex
        self.escrow_id = (
            f"poker:{self.channel.guild.id}:{self.hand_id}"
        )
        self.escrow = cog.baba.new_escrow(
            escrow_id=self.escrow_id, game_type="poker"
        )
        self._forced_bets_posted = False
        self.terminal_state = self.escrow.state
        self._terminal_lock = asyncio.Lock()

    def _assign_seats_and_positions(self):
        """Assign stable clockwise seats and Texas Hold'em positions."""
        count = len(self.players)
        if count < 2:
            raise ValueError("a poker table needs at least two players")
        for index, player in enumerate(self.players):
            player.seat_index = index
            player.position = ""

        button = self.button_seat
        if count == 2:
            self.players[button].position = "SB/BTN"
            self.players[(button + 1) % count].position = "BB"
        else:
            self.players[button].position = "BTN"
            self.players[(button + 1) % count].position = "SB"
            self.players[(button + 2) % count].position = "BB"
            middle_positions = {
                1: ["UTG"],
                2: ["UTG", "CO"],
                3: ["UTG", "HJ", "CO"],
                4: ["UTG", "MP", "HJ", "CO"],
                5: ["UTG", "UTG+1", "MP", "HJ", "CO"],
            }.get(count - 3, [])
            for offset in range(3, count):
                middle_index = offset - 3
                if middle_index < len(middle_positions):
                    position = middle_positions[middle_index]
                else:
                    position = f"Seat+{offset}"
                self.players[(button + offset) % count].position = position

    def _next_seat(self, seat_index: int) -> int:
        return (seat_index + 1) % len(self.players)

    def _seat_with_position(self, position: str) -> int:
        return next(
            player.seat_index
            for player in self.players
            if player.position == position
        )

    def action_order_for(self, street: str | None = None) -> list[int]:
        """Return clockwise actor IDs for the requested betting street."""
        street = street or self.street
        if street == "preflop":
            if len(self.players) == 2:
                start = self.button_seat
            else:
                start = self._next_seat(self._seat_with_position("BB"))
        else:
            start = self._next_seat(self.button_seat)

        ordered: list[int] = []
        seat = start
        for _ in self.players:
            player = self.players[seat]
            if not player.folded and not player.all_in:
                ordered.append(player.id)
            seat = self._next_seat(seat)
        return ordered

    def table_display(self) -> str:
        lines = []
        for player in sorted(self.players, key=lambda item: item.seat_index):
            tags = [player.position]
            if player.is_bot:
                tags.append("BOT")
            if player.folded:
                tags.append("folded")
            elif player.all_in:
                tags.append("all-in")
            if player.id == self.current_actor_id and self.terminal_state == "open":
                tags.append("TO ACT")
            lines.append(
                f"Seat {player.seat_index + 1}: {player.name} — "
                f"{' / '.join(tags)}"
            )
        return "\n".join(lines)

    async def _announce_table(self, *, prefix: str = "🪑 Table"):
        await self.channel.send(f"{prefix}\n{self.table_display()}")

    def _player_for(self, user_id: int):
        return next((p for p in self.players if p.id == user_id), None)

    def _next_actor_after(self, actor_id: int | None) -> int | None:
        order = self.action_order_for(self.street)
        if not order:
            return None
        actor = self._player_for(actor_id) if actor_id is not None else None
        if actor is None:
            return order[0]
        seat = self._next_seat(actor.seat_index)
        for _ in self.players:
            candidate = self.players[seat]
            if not candidate.folded and not candidate.all_in:
                return candidate.id
            seat = self._next_seat(seat)
        return None

    async def _send_action_message(self, sender, content: str):
        await sender(content)

    async def _dispatch_action(
        self,
        player: Player,
        action: str,
        *,
        amount: int = 0,
        sender=None,
    ) -> bool:
        """Apply human and bot actions through the same turn/escrow path."""
        if self.terminal_state != "open":
            if sender:
                await self._send_action_message(sender, "This hand has already ended.")
            return False
        if player.folded:
            if sender:
                await self._send_action_message(sender, "You are not in the hand or already folded.")
            return False
        if player.id != self.current_actor_id:
            expected = self._player_for(self.current_actor_id)
            message = (
                f"It is {expected.name}'s turn."
                if expected else "It is not your turn."
            )
            if sender:
                await self._send_action_message(sender, message)
            return False

        previous_highest = self.highest
        if action == "bet":
            if amount < 1:
                if sender:
                    await self._send_action_message(sender, "Bet must be positive.")
                return False
            if not self.escrow.try_debit(player.id, amount):
                if sender:
                    await self._send_action_message(sender, "Insufficient funds.")
                return False
            player.bet += amount
            if player.bet > self.highest:
                self.highest = player.bet
            if sender:
                await self._send_action_message(
                    sender,
                    f"{player.name} bets {amount}. (Total: {player.bet}) Pot: {self.escrow.total}",
                )
        elif action == "call":
            difference = self.highest - player.bet
            if difference <= 0:
                if sender:
                    await self._send_action_message(
                        sender, "Nothing to call—use `baba check` to check."
                    )
                return False
            if not self.escrow.try_debit(player.id, difference):
                if sender:
                    await self._send_action_message(sender, "Insufficient to call.")
                return False
            player.bet += difference
            if sender:
                await self._send_action_message(
                    sender,
                    f"{player.name} calls {difference}. Pot: {self.escrow.total}",
                )
        elif action == "check":
            if player.bet != self.highest:
                if sender:
                    await self._send_action_message(
                        sender, "You can’t check until you’ve matched the highest bet."
                    )
                return False
            if sender:
                await self._send_action_message(sender, f"{player.name} checks.")
        elif action == "fold":
            player.folded = True
            active = [p for p in self.players if not p.folded]
            if len(active) == 1:
                winner = active[0]
                settled = await self.settle_game(
                    {winner.id: self.escrow.total}, "fold"
                )
                if not settled:
                    player.folded = False
                    if sender:
                        await self._send_action_message(sender, "This hand has already ended.")
                    return False
                if sender:
                    await self._send_action_message(sender, f"{player.name} folds.")
                await self.channel.send(
                    f"{winner.name} wins pot of {self.escrow.total} by default!"
                )
                self._mark_game_inactive()
                return True
            if sender:
                await self._send_action_message(sender, f"{player.name} folds.")
        elif action == "allin":
            available = self.cog.baba.get_money(player.id)
            if available <= 0 or not self.escrow.try_debit(player.id, available):
                if sender:
                    await self._send_action_message(sender, "Insufficient funds.")
                return False
            player.bet += available
            player.all_in = True
            if player.bet > self.highest:
                self.highest = player.bet
            if sender:
                await self._send_action_message(
                    sender,
                    f"{player.name} goes ALL IN {available}! Pot: {self.escrow.total}",
                )
        else:
            raise ValueError(f"unknown poker action: {action}")

        if action == "bet" and player.bet > previous_highest:
            self.acted_players.clear()
        self.acted_players.add(player.id)
        self.pot = self.escrow.total
        if self.terminal_state == "open":
            self.current_actor_id = self._next_actor_after(player.id)
        advanced = await self._maybe_advance_round()
        if not advanced and self.terminal_state == "open":
            await self.run_bot_turns()
        return True

    def _mark_game_inactive(self):
        guild_id = self.channel.guild.id
        game = self.cog.games.get(guild_id)
        if game and game.get("instance") is self:
            game["active"] = False
            game["instance"] = None

    def bot_action(self, player: Player) -> tuple[str, int]:
        """Adapt the RLCard rule decision to the shared action dispatcher."""
        difference = self.highest - player.bet
        stack = self.cog.baba.get_money(player.id)
        if len(player.hand) != 2:
            # A lobby/test fixture can enter a betting round before cards are
            # dealt.  Keep the old safe fallback for that invalid/incomplete
            # state; real hands always use the external rule policy.
            if difference > 0:
                return ("allin", 0) if stack < difference else ("call", 0)
            return "check", 0

        decision = self.bot_strategy.decide(
            player.hand,
            self.community,
            pot=self.escrow.total,
            current_bet=player.bet,
            highest_bet=self.highest,
            stack=stack,
        )
        return decision.action, decision.amount

    async def run_bot_turns(self):
        if getattr(self, "_bot_turn_running", False):
            return
        self._bot_turn_running = True
        try:
            for _ in range(len(self.players) * 2 + 1):
                if self.terminal_state != "open" or self.current_actor_id is None:
                    return
                player = self._player_for(self.current_actor_id)
                if not player or not player.is_bot:
                    return
                action, amount = self.bot_action(player)
                before_actor = self.current_actor_id
                applied = await self._dispatch_action(
                    player, action, amount=amount, sender=self.channel.send
                )
                if not applied and self.current_actor_id == before_actor:
                    await self._dispatch_action(
                        player, "fold", sender=self.channel.send
                    )
        finally:
            self._bot_turn_running = False

    async def refund_game(self, reason: str = "unknown"):
        """Refund the full hand escrow exactly once."""
        async with self._terminal_lock:
            if self.terminal_state != "open":
                return False
            if not self.escrow.refund():
                return False
            self.terminal_state = "refunded"
            self.pot = 0
            return True

    async def settle_game(
        self, payouts: dict[int, int], reason: str = "showdown"
    ):
        """Commit one terminal payout batch exactly once."""
        async with self._terminal_lock:
            if self.terminal_state != "open":
                return False
            if not self.escrow.settle(payouts):
                return False
            self.terminal_state = "settled"
            self.pot = 0
            return True

    def post_forced_bets(self):
        if self._forced_bets_posted:
            return False
        sb = self.players[(self.button_seat + 1) % len(self.players)]
        bb = self.players[(self.button_seat + 2) % len(self.players)]
        if len(self.players) == 2:
            sb = self.players[self.button_seat]
            bb = self.players[(self.button_seat + 1) % len(self.players)]
        debits = {sb.id: self.small_blind, bb.id: self.big_blind}
        if not self.escrow.try_debit_many(debits):
            return False
        for player in self.players:
            player.bet = 0
            player.all_in = False
        sb.bet = self.small_blind
        bb.bet = self.big_blind
        self.pot = self.escrow.total
        self.highest = self.big_blind
        self.street = "preflop"
        self.current_actor_id = self.action_order_for("preflop")[0]
        self._forced_bets_posted = True
        return True

    async def play_game(self):
        if not self.post_forced_bets():
            guild_id = self.channel.guild.id
            game = self.cog.games.get(guild_id)
            if game and game.get('instance') is self:
                game['active'] = False
                game['instance'] = None
            await self.channel.send(
                "Game cancelled because one or more players cannot post the "
                "minimum bet."
            )
            return

        # 1) announce game start
        await self.channel.send("**Game started!** Dealing hands and posting blinds.")
        await self._announce_table(prefix="🪑 Seats / BTN / SB / BB")

        # 2) deal hole cards & post blinds
        for p in self.players:
            p.hand = self.deck.deal(2)

            # # 2a) DM them their cards
            # try:
            #     await p.user.send(embed=discord.Embed(
            #         title="🃏 Your Hole Cards",
            #         description=f"{p.hand[0]}   {p.hand[1]}",
            #         color=discord.Color.gold()
            #     ))
            # except discord.Forbidden:
            #     # DMs blocked, they’ll use the button below
            #     pass

        # 3) add the ephemeral view-button for anyone who missed the DM
        view = discord.ui.View()
        btn = discord.ui.Button(label="View Your Hand", style=discord.ButtonStyle.primary)
        async def view_cb(inter: discord.Interaction):
            pl = next(p for p in self.players if p.id == inter.user.id)
            await inter.response.send_message(
                embed=discord.Embed(
                    title="🃏 Your Hole Cards",
                    description=f"{pl.hand[0]}   {pl.hand[1]}",
                    color=discord.Color.gold()
                ),
                ephemeral=True
            )
        btn.callback = view_cb
        view.add_item(btn)
        await self.channel.send("Click below to view your hole cards (privately):", view=view)

        # 4) now start the first betting round
        await self.betting_round()


    
    async def betting_round(self):
        """Announce a street and begin its clockwise action queue."""
        self.acted_players.clear()
        order = self.action_order_for(self.street)
        if not order:
            return await self.next_round()
        if self.current_actor_id not in order:
            self.current_actor_id = order[0]
        street_name = self.street.title()
        await self.channel.send(
            f"**🃏 {street_name}** — {self._player_for(self.current_actor_id).name} to act.\n"
            f"{self.table_display()}"
        )
        await self.run_bot_turns()


    async def place_bet(self, ctx: commands.Context, amount: int):
        player = next((p for p in self.players if p.id==ctx.author.id and not p.folded), None)
        if not player:
            return await ctx.send("You are not in the game or already folded.")
        await self._dispatch_action(
            player, "bet", amount=amount, sender=ctx.send
        )


    async def call(self, ctx: commands.Context):
        player = next((p for p in self.players if p.id==ctx.author.id and not p.folded), None)
        if not player:
            return await ctx.send("You are not in the game or folded.")
        await self._dispatch_action(player, "call", sender=ctx.send)


    async def check(self, ctx: commands.Context):
        player = next((p for p in self.players if p.id==ctx.author.id), None)
        if not player or player.folded:
            return await ctx.send("You’re not in the hand or have already folded.")
        await self._dispatch_action(player, "check", sender=ctx.send)



    async def fold(self, ctx: commands.Context):
        player = next((p for p in self.players if p.id==ctx.author.id and not p.folded), None)
        if not player:
            return await ctx.send("Not in game or already folded.")
        await self._dispatch_action(player, "fold", sender=ctx.send)


    async def allin(self, ctx: commands.Context):
        player = next((p for p in self.players if p.id==ctx.author.id and not p.folded), None)
        if not player:
            return await ctx.send("Not in game or folded.")
        await self._dispatch_action(player, "allin", sender=ctx.send)



    async def _maybe_advance_round(self) -> bool:
        eligible = [
            p for p in self.players if not p.folded and not p.all_in
        ]
        pending = [p for p in eligible if p.id not in self.acted_players]
        if pending or any(p.bet != self.highest for p in eligible):
            if pending:
                await self.channel.send(
                    "⏳ Waiting on: " + ", ".join(p.name for p in pending)
                )
            return False
        await self.next_round()
        return True


    async def next_round(self):
        self.round += 1

        if self.round == 1:            # flop
            new_cards = self.deck.deal(3)
            street = "Flop"
        elif self.round == 2:          # turn
            new_cards = self.deck.deal(1)
            street = "Turn"
        elif self.round == 3:          # river
            new_cards = self.deck.deal(1)
            street = "River"
        else:                          # showdown
            return await self.showdown()

        # add the new community cards and announce
        self.community.extend(new_cards)
        self.street = street.lower()

        # reset bets & who has acted
        for p in self.players:
            p.bet = 0
        self.highest = 0
        self.acted_players.clear()
        order = self.action_order_for(self.street)
        self.current_actor_id = order[0] if order else None

        await self.channel.send(f"**{street}**: {' '.join(self.community)}")
        await self.betting_round()


    async def showdown(self):
        if self.terminal_state != "open":
            return

        # 1) First, reveal everyone’s hand in an embed
        embed = discord.Embed(title="🏁 Showdown — Player Hands", color=discord.Color.purple())
        for p in self.players:
            hand_str = ' '.join(p.hand)
            status = "(folded)" if p.folded else ""
            embed.add_field(name=f"{p.name} {status}", value=hand_str or "No cards", inline=True)
        await self.channel.send(embed=embed)

        # 2) Determine the winner(s) using your existing ranking logic
        best, best_score = None, None
        for p in self.players:
            if p.folded:
                continue
            score = self.rank_hand(p.hand + self.community)
            if best_score is None or self.compare(score, best_score) > 0:
                best_score, best = score, p

        # 3) Award the pot through the durable, idempotent escrow
        if best:
            pot = self.pot
            settled = await self.settle_game({best.id: pot}, "showdown")
            if not settled:
                return
            await self.channel.send(f"🎉 **{best.name} wins {pot} {self.cog.money_name}!**")

            guild_id = self.channel.guild.id
            game = self.cog.games.get(guild_id)
            if game and game.get('instance') is self:
                game['active'] = False
                game['instance'] = None
        else:
            await self.channel.send("No winner could be determined.")

        # 4) Clean up for next game
        guild_id = self.channel.guild.id
        game = self.cog.games.get(guild_id)
        if game and game.get('instance') is self:
            game['active'] = False

    
    def rank_hand(self, cards: list[str]) -> tuple[int, list[int]]:
        """
        输入 7 张（或更多）牌面字符串，比如 ['A♠','K♦','2♣',…]。
        返回 (hand_rank, tiebreakers)：
        hand_rank: 0–9，9 = 皇家同花顺，8=同花顺，7=四条…0=高牌
        tiebreakers: 用于平级比较的值列表，越高越好
        """
        # 先把点数和花色分别提取出来
        vals = [self.deck.value(c) for c in cards]
        suits = [self.deck.suit(c)  for c in cards]
        cnt = {v: vals.count(v) for v in set(vals)}
        # 按出现次数和点数排序，方便找对子/三条/四条
        groups = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
        # 检测 Flush
        flush_suit = next((s for s in set(suits) if suits.count(s) >= 5), None)
        flush_vals = sorted([v for v,c in zip(vals,suits) if c==flush_suit], reverse=True) if flush_suit else []
        # 检测 Straight（含 A-2-3-4-5）
        def _is_straight(vs: list[int]) -> int:
            sv = sorted(set(vs))
            # 把 A 当作 1 试一次
            if 14 in sv:
                sv = [1] + sv
            max_run = 1
            run = 1
            for i in range(1, len(sv)):
                if sv[i] == sv[i-1] + 1:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 1
            if max_run >= 5:
                # 找到最高顺子顶点
                for i in range(len(sv)-1, 3, -1):
                    if sv[i] - sv[i-4] == 4:
                        return sv[i]
            return 0
        straight_high = _is_straight(vals)
        # 同花顺？
        if flush_suit and straight_high:
            # flush_vals 中找顺子
            sf_high = _is_straight(flush_vals)
            if sf_high:
                return (8, [sf_high])  # 8=同花顺

        # 四条？
        if groups[0][1] == 4:
            four = groups[0][0]
            kicker = max(v for v in vals if v != four)
            return (7, [four, kicker])

        # 葫芦（三条+一对）？
        if groups[0][1] == 3 and groups[1][1] >= 2:
            three = groups[0][0]
            pair  = groups[1][0]
            return (6, [three, pair])

        # 同花？
        if flush_suit:
            return (5, flush_vals[:5])

        # 顺子？
        if straight_high:
            return (4, [straight_high])

        # 三条？
        if groups[0][1] == 3:
            kickers = sorted([v for v in vals if v != groups[0][0]], reverse=True)[:2]
            return (3, [groups[0][0]] + kickers)

        # 两对？
        if groups[0][1] == 2 and groups[1][1] == 2:
            high_pair, low_pair = groups[0][0], groups[1][0]
            kicker = max(v for v in vals if v not in (high_pair, low_pair))
            return (2, [high_pair, low_pair, kicker])

        # 一对？
        if groups[0][1] == 2:
            pair = groups[0][0]
            kickers = sorted([v for v in vals if v != pair], reverse=True)[:3]
            return (1, [pair] + kickers)

        # 高牌
        top5 = sorted(vals, reverse=True)[:5]
        return (0, top5)


        
    def compare(self, a: tuple[int, list[int]], b: tuple[int, list[int]]) -> int:
        """
        比较两个 rank_hand 的输出：
        返回  1  如果 a > b
                0  如果 a == b
                -1  如果 a < b
        """
        if a[0] != b[0]:
            return 1 if a[0] > b[0] else -1
        # 同级别时逐一比点数 tiebreakers
        for x, y in zip(a[1], b[1]):
            if x != y:
                return 1 if x > y else -1
        return 0
    
# --- Setup ---
async def setup(bot: commands.Bot):
    await bot.add_cog(PokerCog(bot))
    print('PokerCog loaded!')
