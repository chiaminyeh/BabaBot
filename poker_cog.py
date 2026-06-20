
import discord
import os
import logging
import random
import asyncio
from datetime import datetime
from discord import app_commands
from discord.ext import commands, tasks

# --- Player Class ---
class Player:
    def __init__(self, user: discord.User):
        self.user = user
        self.id = user.id
        self.name = user.name
        self.hand: list[str] = []
        self.bet: int = 0
        self.folded: bool = False
        

# --- Poker Cog ---
class PokerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
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

    # --- Slash: Start Poker ---
    @app_commands.command(name='start_poker')
    @app_commands.describe(
        min_players="How many players required to start the game (2–10)"
    )
    async def start_poker(
        self,
        interaction: discord.Interaction,
        min_players: app_commands.Range[int, 2, 10] = 4
    ):
        
        """Start a new poker game, waiting for `min_players` to join."""
        guild_id = interaction.guild_id
        if guild_id in self.games and self.games[guild_id]['active']:
            return await interaction.response.send_message(
                "A game is already in progress!", ephemeral=True
            )

        # initialize game state with dynamic player count
        self.games[guild_id] = {
            'active': True,
            'players': [],            # list[Player]
            'channel_id': interaction.channel_id,
            'instance': None,         # PokerGame instance
            'last_action': datetime.now(),
            'min_players': min_players
        }

        embed = discord.Embed(
            title="Poker Game",
            description=(
                f"Waiting for **{min_players}** players to join\n"
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
            bal = self.bank.get(user.id, (0,))[0]
            game = self.games[guild_id]

            if bal < self.minimum_bet:
                return await btn_inter.response.send_message(
                    f"You need at least {self.minimum_bet} {self.money_name} to join.", ephemeral=True
                )
            if any(p.id == user.id for p in game['players']):
                return await btn_inter.response.send_message("Already joined!", ephemeral=True)

            game['players'].append(Player(user))
            game['last_action'] = datetime.now()
            await btn_inter.response.send_message(f"{user.name} joined! ({len(game['players'])}/{min_players})")

            if len(game['players']) >= game['min_players']:
                await self._begin_game(guild_id)

        btn.callback = btn_cb
        view.add_item(btn)
        await msg.edit(view=view)

        # wait up to 60s for enough joins
        start = datetime.now()
        while (datetime.now() - start).seconds < 60:
            await asyncio.sleep(1)
            game = self.games[guild_id]
            if len(game['players']) >= game['min_players']:
                return await self._begin_game(guild_id)

        # timeout: not enough players
        self.games[guild_id]['active'] = False
        await interaction.followup.send(
            f"Timed out after 60s — only {len(self.games[guild_id]['players'])}/{min_players} joined."
        )

    async def _begin_game(self, guild_id: int):
        game = self.games[guild_id]
        if game['instance']:
            return
        channel = self.bot.get_channel(game['channel_id'])
        players = game['players']
        poker = PokerGame(channel, players, self)
        game['instance'] = poker
        game['last_action'] = datetime.now()
        await poker.play_game()

    # --- Slash: End Poker ---
    @app_commands.command(name='end_poker')
    @commands.has_permissions(administrator=True)
    async def end_poker(self, interaction: discord.Interaction):
        """Force-end the poker game and refund bets."""
        guild_id = interaction.guild_id
        game = self.games.get(guild_id)
        if not game or not game['active']:
            return await interaction.response.send_message("No active game.")
        # refund
        inst = game['instance']
        if inst and inst.pot > 0:
            for p in inst.players:
                if p.bet > 0:
                    bal, claimed = self.bank[p.id]
                    self.bank[p.id] = (bal + p.bet, claimed)
        game['active'] = False
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
                chan = self.bot.get_channel(game['channel_id'])
                await chan.send("Game ended due to inactivity.")
                # refund
                inst = game['instance']
                if inst and inst.pot > 0:
                    for p in inst.players:
                        if p.bet > 0:
                            bal, claimed = self.bank[p.id]
                            self.bank[p.id] = (bal + p.bet, claimed)
                game['active'] = False

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
    def __init__(self, channel: discord.TextChannel, players: list[Player], cog: PokerCog):
        self.channel = channel
        self.players = players
        self.cog = cog
        self.deck = Deck()
        self.community: list[str] = []
        self.pot: int = 0
        self.highest: int = cog.minimum_bet
        self.round = 0
        self.acted_players: set[int] = set()
        active_ids = {p.id for p in self.players if not p.folded}
        pending_ids = active_ids - self.acted_players
        pending_names = [p.name for p in self.players if p.id in pending_ids]


    async def play_game(self):
        # 1) announce game start
        await self.channel.send("**Game started!** Dealing hands and posting blinds.")

        # 2) deal hole cards & post blinds
        for p in self.players:
            p.hand = self.deck.deal(2)
            bal, _ = self.cog.bank[p.id]
            self.cog.bank[p.id] = (bal - self.cog.minimum_bet, False)
            p.bet = self.cog.minimum_bet
            self.pot += self.cog.minimum_bet

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
        # Reset who’s acted this round:
        self.acted_players.clear()
        # Pre-flop betting prompt
        await self.channel.send("**🃏 Pre-Flop** — place your bets now!")
        # We rely on place_bet/call/fold/allin to call next_round() once everyone has matched/folded.


    async def place_bet(self, ctx: commands.Context, amount: int):
        player = next((p for p in self.players if p.id==ctx.author.id and not p.folded), None)
        if not player:
            return await ctx.send("You are not in the game or already folded.")
        if amount < 1:
            return await ctx.send("Bet must be positive.")
        bal, claimed = self.cog.bank[player.id]
        if amount > bal:
            return await ctx.send("Insufficient funds.")
        bal -= amount; self.cog.bank[player.id] = (bal, claimed)
        player.bet += amount; self.pot += amount
        if player.bet > self.highest:
            self.highest = player.bet
        await ctx.send(f"{player.name} bets {amount}. (Total: {player.bet}) Pot: {self.pot}")
        # continue if all matched or folded
        self.acted_players.add(ctx.author.id)
        # recompute pending:
        
        active_ids = {p.id for p in self.players if not p.folded}
        pending_ids = active_ids - self.acted_players

        if pending_ids:
            # someone still hasn’t moved
            await self.channel.send(
                "⏳ Waiting on: " + ", ".join(
                    p.name for p in self.players if p.id in pending_ids
                )
            )
        else:
            # all have acted and bets are matched
            if all(p.bet == self.highest or p.folded for p in self.players):
                await self.next_round()


    async def call(self, ctx: commands.Context):
        player = next((p for p in self.players if p.id==ctx.author.id and not p.folded), None)
        if not player:
            return await ctx.send("You are not in the game or folded.")
        diff = self.highest - player.bet
        if diff == 0:
            return await ctx.send("Nothing to call—use `baba check` to check.")

        bal, claimed = self.cog.bank[player.id]
        if diff > bal:
            return await ctx.send("Insufficient to call.")
        bal -= diff; self.cog.bank[player.id] = (bal, claimed)
        player.bet += diff; self.pot += diff
        await ctx.send(f"{player.name} calls {diff}. Pot: {self.pot}")
        self.acted_players.add(ctx.author.id)

        # recompute pending:
        active_ids = {p.id for p in self.players if not p.folded}
        pending_ids = active_ids - self.acted_players

        if pending_ids:
            # someone still hasn’t moved
            await self.channel.send(
                "⏳ Waiting on: " + ", ".join(
                    p.name for p in self.players if p.id in pending_ids
                )
            )
        else:
            # all have acted and bets are matched
            if all(p.bet == self.highest or p.folded for p in self.players):
                await self.next_round()


    async def check(self, ctx: commands.Context):
        player = next((p for p in self.players if p.id==ctx.author.id), None)
        if not player or player.folded:
            return await ctx.send("You’re not in the hand or have already folded.")
        if player.bet != self.highest:
            return await ctx.send("You can’t check until you’ve matched the highest bet.")
        await ctx.send(f"{player.name} checks.")
        # now only advance once _all_ active players have either folded or their bet == highest
        self.acted_players.add(ctx.author.id)

        # recompute pending:
        active_ids = {p.id for p in self.players if not p.folded}
        pending_ids = active_ids - self.acted_players

        if pending_ids:
            # someone still hasn’t moved
            await self.channel.send(
                "⏳ Waiting on: " + ", ".join(
                    p.name for p in self.players if p.id in pending_ids
                )
            )
        else:
            # all have acted and bets are matched
            if all(p.bet == self.highest or p.folded for p in self.players):
                await self.next_round()



    async def fold(self, ctx: commands.Context):
        player = next((p for p in self.players if p.id==ctx.author.id and not p.folded), None)
        if not player:
            return await ctx.send("Not in game or already folded.")
        player.folded = True
        await ctx.send(f"{player.name} folds.")
        active = [p for p in self.players if not p.folded]
        if len(active)==1:
            winner = active[0]
            bal, claimed = self.cog.bank[winner.id]
            self.cog.bank[winner.id] = (bal + self.pot, claimed)
            await self.channel.send(f"{winner.name} wins pot of {self.pot} by default!")
            guild_id = self.channel.guild.id
            self.cog.games[guild_id]['active'] = False
            self.cog.games[guild_id]['instance'] = None
            return
        self.acted_players.add(ctx.author.id)

        # recompute pending:
        active_ids = {p.id for p in self.players if not p.folded}
        pending_ids = active_ids - self.acted_players

        if pending_ids:
            # someone still hasn’t moved
            await self.channel.send(
                "⏳ Waiting on: " + ", ".join(
                    p.name for p in self.players if p.id in pending_ids
                )
            )
        else:
            # all have acted and bets are matched
            if all(p.bet == self.highest or p.folded for p in self.players):
                await self.next_round()


    async def allin(self, ctx: commands.Context):
        player = next((p for p in self.players if p.id==ctx.author.id and not p.folded), None)
        if not player:
            return await ctx.send("Not in game or folded.")
        bal, claimed = self.cog.bank[player.id]
        diff = bal
        player.bet += diff; self.pot += diff
        self.cog.bank[player.id] = (0, claimed)
        if player.bet > self.highest:
            self.highest = player.bet
        await ctx.send(f"{player.name} goes ALL IN {diff}! Pot: {self.pot}")
        self.acted_players.add(ctx.author.id)
        # recompute pending:
        active_ids = {p.id for p in self.players if not p.folded}
        pending_ids = active_ids - self.acted_players

        if pending_ids:
            # someone still hasn’t moved
            await self.channel.send(
                "⏳ Waiting on: " + ", ".join(
                    p.name for p in self.players if p.id in pending_ids
                )
            )
        else:
            # all have acted and bets are matched
            if all(p.bet == self.highest or p.folded for p in self.players):
                await self.next_round()


    def _ready_for_next(self) -> bool:
        active_ids = {p.id for p in self.players if not p.folded}
        # 1) Everyone still in the hand has acted at least once
        if not active_ids.issubset(self.acted_players):
            return False
        # 2) All bets are matched
        return all(p.folded or p.bet == self.highest for p in self.players)
    


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
        await self.channel.send(f"**{street}**: {' '.join(self.community)}")

        # reset bets & who has acted
        for p in self.players:
            p.bet = 0
        self.highest = 0
        self.acted_players.clear()

        # prompt for the next betting round
        await self.channel.send("Place your bets!")


    async def showdown(self):
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

        # 3) Award the pot
        if best:
            bal, claimed = self.cog.bank[best.id]
            self.cog.bank[best.id] = (bal + self.pot, claimed)
            await self.channel.send(f"🎉 **{best.name} wins {self.pot} {self.cog.money_name}!**")

              # after announcing the winner…
            guild_id = self.channel.guild.id
            # deactivate & clean up
            self.cog.games[guild_id]['active'] = False
            self.cog.games[guild_id]['instance'] = None
        else:
            await self.channel.send("No winner could be determined.")

        # 4) Clean up for next game
        self.cog.games[self.cog.bot.guild_id]['active'] = False

    
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


        
    def is_straight(self, values: list[int]) -> bool:
        """
        只要判断 values 列表里有没有顺子（不关花色），返回 True/False。
        """
        unique = sorted(set(values))
        if 14 in unique:
            unique = [1] + unique
        run = 1
        for i in range(1, len(unique)):
            run = run + 1 if unique[i] == unique[i-1] + 1 else 1
            if run >= 5:
                return True
        return False


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
