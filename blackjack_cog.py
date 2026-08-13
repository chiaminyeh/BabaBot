import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Dict, Optional
import asyncio
import uuid

# filepath: c:\Users\manza\Downloads\Bababot\Bot\blackjack.py
from poker_cog import Deck  # Assuming this exists as per your previous file

# --- UI Components ---

class BlackjackView(discord.ui.View):
    def __init__(self, game_instance):
        super().__init__(timeout=300) # 5 minute timeout matching game timeout
        self.game = game_instance

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.success, emoji="🃏")
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.game.handle_action(interaction, "hit")

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.game.handle_action(interaction, "stand")

    @discord.ui.button(label="Double", style=discord.ButtonStyle.primary, emoji="💰")
    async def double_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.game.handle_action(interaction, "double")

# --- Game Logic Classes ---

class BlackjackPlayer:
    def __init__(self, user, bet):
        self.user = user
        self.id = user.id
        self.name = user.name
        self.hand = []
        self.bet = bet
        self.stood = False
        self.busted = False
        self.doubled = False
        self.has_acted = False # Used to track if they are currently thinking

    def get_hand_value(self) -> int:
        value = 0
        aces = 0
        
        for card in self.hand:
            card_val = card[:-1]  # Remove suit
            if card_val in ['J', 'Q', 'K']:
                value += 10
            elif card_val == 'A':
                aces += 1
            else:
                value += int(card_val)
        
        # Calculate optimal ace values
        for _ in range(aces):
            if value + 11 <= 21:
                value += 11
            else:
                value += 1
        return value

    def format_hand(self, hide=False):
        if not self.hand:
            return "Empty"
        if hide:
            return f"{self.hand[0]} 🂠"
        return " ".join(self.hand)

class BlackjackGame:
    def __init__(
        self,
        cog,
        channel,
        players: List[BlackjackPlayer],
        *,
        hand_id: str | None = None,
    ):
        self.cog = cog
        self.channel = channel
        self.players = players
        self.deck = Deck()
        self.dealer_hand = []
        self.message: Optional[discord.Message] = None
        self.view: Optional[BlackjackView] = None
        self.active = True
        self.turn_index = 0 # Not strictly used in simultaneous play, but good for tracking
        self.hand_id = hand_id or uuid.uuid4().hex
        self.escrow_id = (
            f"blackjack:{self.channel.guild.id}:{self.hand_id}"
        )
        self.escrow = cog.baba.new_escrow(
            escrow_id=self.escrow_id,
            game_type="blackjack",
        )
        self.terminal_state = self.escrow.state
        self.active = self.terminal_state == "open"
        self._terminal_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()

    async def refund_game(self, reason: str = "unknown") -> bool:
        async with self._terminal_lock:
            if self.terminal_state != "open":
                return False
            if not self.escrow.refund():
                return False
            self.terminal_state = "refunded"
            self.active = False
            return True

    async def start(self):
        # Deal initial cards
        self.dealer_hand = self.deck.deal(2)
        for player in self.players:
            player.hand = self.deck.deal(2)

        # Check for Dealer Blackjack immediately
        dealer_val = self._calculate_hand(self.dealer_hand)
        if dealer_val == 21:
            await self.end_round(dealer_blackjack=True)
            return

        # Check for Player Blackjacks (Natural)
        for player in self.players:
            if player.get_hand_value() == 21:
                player.stood = True # Auto stand on 21
        
        # If everyone has blackjack, end immediately
        if all(p.stood for p in self.players):
            await self.end_round()
            return

        self.view = BlackjackView(self)
        embed = self.build_embed()
        self.message = await self.channel.send(embed=embed, view=self.view)

    async def handle_action(self, interaction: discord.Interaction, action: str):
        """Serialize player callbacks so duplicate submissions debit at most once."""
        async with self._action_lock:
            return await self._handle_action_locked(interaction, action)

    async def _handle_action_locked(self, interaction: discord.Interaction, action: str):
        if self.terminal_state != "open" or not self.active:
            return await interaction.response.send_message(
                "This game has already ended.",
                ephemeral=True,
                delete_after=5,
            )
        # Find player
        player = next((p for p in self.players if p.id == interaction.user.id), None)
        
        if not player:
            return await interaction.response.send_message("You are not in this game!", ephemeral=True, delete_after=5)
        
        if player.stood or player.busted:
            return await interaction.response.send_message("You have already finished your turn.", ephemeral=True, delete_after=5)

        if action == "hit":
            player.hand.extend(self.deck.deal(1))
            val = player.get_hand_value()
            if val > 21:
                player.busted = True
                msg = "Busted!"
            elif val == 21:
                player.stood = True
                msg = "21! Auto-standing."
            else:
                msg = f"Hit! Total: {val}"
            
            await interaction.response.send_message(msg, ephemeral=True, delete_after=5)

        elif action == "stand":
            player.stood = True
            await interaction.response.send_message(f"Stood at {player.get_hand_value()}.", ephemeral=True, delete_after=5)

        elif action == "double":
            if player.doubled:
                return await interaction.response.send_message(
                    "You have already doubled down.",
                    ephemeral=True,
                    delete_after=5,
                )

            # Debit the additional stake through the same durable escrow as the
            # opening bet.  Do not read and mutate the legacy bank directly.
            if not self.escrow.try_debit(player.id, player.bet):
                return await interaction.response.send_message("Not enough funds to double down!", ephemeral=True, delete_after=5)

            player.bet *= 2
            player.doubled = True
            
            # One card only
            player.hand.extend(self.deck.deal(1))
            val = player.get_hand_value()
            
            if val > 21:
                player.busted = True
            player.stood = True # Forced stand after double
            
            await interaction.response.send_message(f"Doubled down! Total: {val}", ephemeral=True, delete_after=5)

        # Update UI
        await self.update_ui()

        # Check if everyone is done
        if all(p.stood or p.busted for p in self.players):
            await self.dealer_play()

    async def update_ui(self):
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self.view)
            except discord.NotFound:
                pass # Message deleted

    async def dealer_play(self):
        # Disable buttons
        if self.view:
            for child in self.view.children:
                child.disabled = True
            await self.update_ui()

        # Dealer logic
        while self._calculate_hand(self.dealer_hand) < 17:
            await asyncio.sleep(1) # Suspense
            self.dealer_hand.extend(self.deck.deal(1))
            await self.update_ui()
        
        await asyncio.sleep(1)
        await self.end_round()

    async def end_round(self, dealer_blackjack=False):
        async with self._terminal_lock:
            if self.terminal_state != "open":
                return False

            dealer_val = self._calculate_hand(self.dealer_hand)
            dealer_busted = dealer_val > 21
            results_text = []
            payouts = {}

            for player in self.players:
                p_val = player.get_hand_value()
                winnings = 0

                if dealer_blackjack:
                    if p_val == 21 and len(player.hand) == 2:
                        winnings = player.bet
                        result = "Push (Both Blackjack)"
                    else:
                        result = "Loss (Dealer Blackjack)"
                elif player.busted:
                    result = "Busted"
                elif dealer_busted:
                    winnings = player.bet * 2
                    result = "Win (Dealer Bust)"
                elif p_val > dealer_val:
                    if p_val == 21 and len(player.hand) == 2:
                        winnings = int(player.bet * 2.5)
                        result = "Blackjack!"
                    else:
                        winnings = player.bet * 2
                        result = "Win"
                elif p_val == dealer_val:
                    winnings = player.bet
                    result = "Push"
                else:
                    result = "Loss"

                if winnings > 0:
                    payouts[player.id] = payouts.get(player.id, 0) + winnings
                results_text.append(
                    f"**{player.name}**: {result} "
                    f"({winnings} {self.cog.money_name})"
                )

            # The escrow transition is the durable exactly-once settlement
            # marker.  An empty payout is still persisted as a terminal loss.
            if not self.escrow.settle(payouts):
                return False
            self.terminal_state = "settled"
            self.active = False

        embed = self.build_embed(show_dealer=True)
        embed.add_field(
            name="🏆 Results", value="\n".join(results_text), inline=False
        )
        embed.color = discord.Color.gold()

        if self.message:
            await self.message.edit(embed=embed, view=None)

        self.cog.remove_game(self.channel.guild.id)
        return True

    def build_embed(self, show_dealer=False):
        embed = discord.Embed(title="🎰 Blackjack", color=discord.Color.blue())
        
        # Dealer
        dealer_val = self._calculate_hand(self.dealer_hand)
        if show_dealer or not self.active:
            d_text = f"{' '.join(self.dealer_hand)} (Total: {dealer_val})"
        else:
            d_text = f"{self.dealer_hand[0]} 🂠 (?)"
        
        embed.add_field(name="👨‍💼 Dealer", value=d_text, inline=False)
        
        # Players
        for p in self.players:
            status = ""
            if p.busted: status = "💥 BUST"
            elif p.stood: status = "🛑 STAND"
            elif p.doubled: status = "💰 DOUBLE"
            
            val = p.get_hand_value()
            embed.add_field(
                name=f"{p.name} {status}", 
                value=f"Cards: {' '.join(p.hand)}\nValue: {val}\nBet: {p.bet}", 
                inline=True
            )
        
        return embed

    def _calculate_hand(self, hand):
        # Helper for dealer hand calc
        val = 0
        aces = 0
        for card in hand:
            c = card[:-1]
            if c in ['J','Q','K']: val += 10
            elif c == 'A': aces += 1
            else: val += int(c)
        for _ in range(aces):
            if val + 11 <= 21: val += 11
            else: val += 1
        return val

# --- Main Cog ---

class BlackjackCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.baba = self.bot.baba
        self.bank = self.bot.baba.bank
        self.money_name = self.bot.baba.money_name
        self.games: Dict[int, BlackjackGame] = {} # Guild ID -> Game
        self.minimum_bet = 10

    def remove_game(self, guild_id):
        if guild_id in self.games:
            del self.games[guild_id]

    @app_commands.command(name='blackjack')
    @app_commands.describe(bet="Amount to bet")
    async def blackjack(self, interaction: discord.Interaction, bet: int):
        """Start a game of Blackjack immediately (Solo or wait for others logic can be added)."""
        guild_id = interaction.guild.id
        
        # 1. Concurrency Check
        if guild_id in self.games:
            return await interaction.response.send_message("A game is already in progress in this server!", ephemeral=True, delete_after=5)

        # 2. Validate the requested stake without reading a stale raw-bank
        # snapshot. The escrow performs the authoritative balance check.
        if bet < self.minimum_bet:
            return await interaction.response.send_message(f"Minimum bet is {self.minimum_bet}!", ephemeral=True, delete_after=5)

        # 3. Setup the durable escrow before any network await.
        # Note: This implementation starts a solo game immediately for smoother UX.
        # To make it multiplayer, you would add a "Join Phase" View here similar to the original code,
        # but for simplicity and speed, this is a direct start.
        
        player = BlackjackPlayer(interaction.user, bet)
        game = BlackjackGame(
            self,
            interaction.channel,
            [player],
            hand_id=f"solo-{interaction.user.id}:{uuid.uuid4().hex}",
        )
        if not game.escrow.try_debit(player.id, bet):
            return await interaction.response.send_message("Insufficient funds!", ephemeral=True, delete_after=5)
        self.games[guild_id] = game
        
        try:
            await interaction.response.send_message(f"Starting Blackjack with bet {bet} {self.money_name}...", ephemeral=True, delete_after=5)
            await game.start()
        except Exception:
            await game.refund_game("start-failure")
            self.remove_game(guild_id)
            raise

    @app_commands.command(name='blackjack_multiplayer')
    @app_commands.describe(max_players="Max players (1-5)")
    async def blackjack_multi(self, interaction: discord.Interaction, max_players: app_commands.Range[int, 1, 5] = 3):
        """Start a multiplayer lobby."""
        guild_id = interaction.guild.id
        if guild_id in self.games:
            return await interaction.response.send_message("Game in progress!", ephemeral=True, delete_after=5)

        # Lobby State
        lobby_players: List[BlackjackPlayer] = []
        lobby_id = f"lobby-{interaction.user.id}-{uuid.uuid4().hex}"
        lobby_escrow = self.baba.new_escrow(
            escrow_id=f"blackjack:{guild_id}:{lobby_id}",
            game_type="blackjack",
        )
        lobby_state = {
            "kind": "blackjack-lobby",
            "hand_id": lobby_id,
            "escrow": lobby_escrow,
            "players": lobby_players,
        }
        self.games[guild_id] = lobby_state
        
        embed = discord.Embed(
            title="Blackjack Lobby", 
            description=f"Waiting for players...\nMax: {max_players}\nMin Bet: {self.minimum_bet}",
            color=discord.Color.green()
        )
        
        view = discord.ui.View(timeout=60)
        
        # Join Button Logic
        async def join_callback(btn_inter: discord.Interaction):
            # Prompt for bet via Modal or follow-up
            # For simplicity in UI, we'll ask for a bet in chat or fixed amount?
            # Let's use a Modal for the cleanest UI
            
            if any(p.id == btn_inter.user.id for p in lobby_players):
                return await btn_inter.response.send_message("Already joined!", ephemeral=True, delete_after=5)

            if view.is_finished():
                return await btn_inter.response.send_message("This lobby is closed.", ephemeral=True, delete_after=5)

            modal = BetModal(
                self,
                lobby_players,
                max_players,
                view,
                msg,
                lobby_escrow,
            )
            await btn_inter.response.send_modal(modal)

        join_btn = discord.ui.Button(label="Join", style=discord.ButtonStyle.primary)
        join_btn.callback = join_callback
        
        start_btn = discord.ui.Button(label="Start Now", style=discord.ButtonStyle.success, disabled=True)
        
        async def start_callback(btn_inter: discord.Interaction):
            if btn_inter.user.id != interaction.user.id:
                return await btn_inter.response.send_message("Only the host can start early.", ephemeral=True, delete_after=5)
            await btn_inter.response.defer()
            view.stop()

        start_btn.callback = start_callback

        view.add_item(join_btn)
        view.add_item(start_btn)

        try:
            await interaction.response.send_message(embed=embed, view=view)
            msg = await interaction.original_response()
        except Exception:
            lobby_escrow.refund()
            self.remove_game(guild_id)
            raise

        async def start_game_logic():
            if self.games.get(guild_id) is not lobby_state:
                return
            if not lobby_players:
                lobby_escrow.refund()
                self.remove_game(guild_id)
                await msg.edit(content="No players joined. Cancelled.", view=None, embed=None)
                return

            # Create actual game
            game = BlackjackGame(
                self,
                interaction.channel,
                lobby_players,
                hand_id=lobby_id,
            )
            self.games[guild_id] = game
            try:
                await msg.delete() # Clean up lobby
                await game.start()
            except Exception:
                await game.refund_game("lobby-start-failure")
                self.remove_game(guild_id)
                raise

        # Wait for view to timeout or stop
        timed_out = await view.wait()
        if timed_out and not lobby_players:
            lobby_escrow.refund()
            if self.games.get(guild_id) is lobby_state:
                self.remove_game(guild_id)
            await msg.edit(content="Lobby timed out.", view=None, embed=None)
        else:
            await start_game_logic()

class BetModal(discord.ui.Modal, title="Place your Bet"):
    bet_amount = discord.ui.TextInput(label="Amount", placeholder="10", min_length=1, max_length=10)

    def __init__(self, cog, lobby_list, max_p, view, message, escrow):
        super().__init__()
        self.cog = cog
        self.lobby = lobby_list
        self.max_p = max_p
        self.view = view
        self.message = message
        self.escrow = escrow

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.bet_amount.value)
        except ValueError:
            return await interaction.response.send_message("Invalid number.", ephemeral=True, delete_after=5)

        if amount < self.cog.minimum_bet:
            return await interaction.response.send_message(f"Min bet is {self.cog.minimum_bet}.", ephemeral=True, delete_after=5)
        if not self.escrow.try_debit(interaction.user.id, amount):
            return await interaction.response.send_message("Insufficient funds.", ephemeral=True, delete_after=5)

        # Add to lobby
        self.lobby.append(BlackjackPlayer(interaction.user, amount))
        
        # Update Lobby UI
        embed = self.message.embeds[0]
        embed.description = f"Players: {len(self.lobby)}/{self.max_p}\n" + "\n".join([f"- {p.name}: {p.bet}" for p in self.lobby])
        
        # Enable start button if players > 0
        self.view.children[1].disabled = False 
        
        await self.message.edit(embed=embed, view=self.view)
        await interaction.response.send_message(f"Joined with {amount}!", ephemeral=True, delete_after=5)

        if len(self.lobby) >= self.max_p:
            self.view.stop() # Auto start
            # The wait() in the main command will trigger start_game_logic

async def setup(bot):
    await bot.add_cog(BlackjackCog(bot))
    print('BlackJack loaded!')