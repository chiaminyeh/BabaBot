import discord
import os
import logging
from discord import app_commands
from discord.ext import commands, tasks
from typing import List, Dict, Tuple, Optional
import random
import asyncio
from datetime import datetime, timedelta

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
    def __init__(self, cog, channel, players: List[BlackjackPlayer]):
        self.cog = cog
        self.channel = channel
        self.players = players
        self.deck = Deck()
        self.dealer_hand = []
        self.message: Optional[discord.Message] = None
        self.view: Optional[BlackjackView] = None
        self.active = True
        self.turn_index = 0 # Not strictly used in simultaneous play, but good for tracking

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
            # Check funds
            current_bal = self.cog.bank.get(player.id, (0,))[0]
            if current_bal < player.bet:
                return await interaction.response.send_message("Not enough funds to double down!", ephemeral=True, delete_after=5)
            
            # Deduct extra bet
            self.cog.bank[player.id] = (current_bal - player.bet, self.cog.bank[player.id][1])
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
        self.active = False
        dealer_val = self._calculate_hand(self.dealer_hand)
        dealer_busted = dealer_val > 21

        results_text = []

        for player in self.players:
            p_val = player.get_hand_value()
            winnings = 0
            
            if dealer_blackjack:
                if p_val == 21 and len(player.hand) == 2:
                    # Push
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
                # Blackjack pays 3:2 usually, but simple 2:1 here unless natural
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
                current, claimed = self.cog.bank.get(player.id, (0, False))
                self.cog.bank[player.id] = (current + winnings, claimed)
            
            results_text.append(f"**{player.name}**: {result} ({winnings} {self.cog.money_name})")

        # Final Embed
        embed = self.build_embed(show_dealer=True)
        embed.add_field(name="🏆 Results", value="\n".join(results_text), inline=False)
        embed.color = discord.Color.gold()
        
        if self.message:
            await self.message.edit(embed=embed, view=None) # Remove buttons
        
        # Save bank
        self.cog.baba.refresh_bank_file()
        
        # Cleanup from cog
        self.cog.remove_game(self.channel.guild.id)

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

        # 2. Money Check
        user_bal = self.bank.get(interaction.user.id, (0,))[0]
        if bet < self.minimum_bet:
            return await interaction.response.send_message(f"Minimum bet is {self.minimum_bet}!", ephemeral=True, delete_after=5)
        if user_bal < bet:
            return await interaction.response.send_message("Insufficient funds!", ephemeral=True, delete_after=5)

        # 3. Deduct Money (Escrow)
        self.bank[interaction.user.id] = (user_bal - bet, self.bank[interaction.user.id][1])

        # 4. Setup Game
        # Note: This implementation starts a solo game immediately for smoother UX.
        # To make it multiplayer, you would add a "Join Phase" View here similar to the original code,
        # but for simplicity and speed, this is a direct start.
        
        player = BlackjackPlayer(interaction.user, bet)
        game = BlackjackGame(self, interaction.channel, [player])
        self.games[guild_id] = game
        
        await interaction.response.send_message(f"Starting Blackjack with bet {bet} {self.money_name}...", ephemeral=True, delete_after=5)
        await game.start()

    @app_commands.command(name='blackjack_multiplayer')
    @app_commands.describe(max_players="Max players (1-5)")
    async def blackjack_multi(self, interaction: discord.Interaction, max_players: app_commands.Range[int, 1, 5] = 3):
        """Start a multiplayer lobby."""
        guild_id = interaction.guild.id
        if guild_id in self.games:
            return await interaction.response.send_message("Game in progress!", ephemeral=True, delete_after=5)

        # Lobby State
        lobby_players: List[BlackjackPlayer] = []
        
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

            modal = BetModal(self, lobby_players, max_players, view, msg)
            await btn_inter.response.send_modal(modal)

        join_btn = discord.ui.Button(label="Join", style=discord.ButtonStyle.primary)
        join_btn.callback = join_callback
        
        start_btn = discord.ui.Button(label="Start Now", style=discord.ButtonStyle.success, disabled=True)
        
        async def start_callback(btn_inter: discord.Interaction):
            if btn_inter.user.id != interaction.user.id:
                return await btn_inter.response.send_message("Only the host can start early.", ephemeral=True, delete_after=5)
            view.stop()
            await btn_inter.response.defer()
            await start_game_logic()

        start_btn.callback = start_callback

        view.add_item(join_btn)
        view.add_item(start_btn)

        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()

        async def start_game_logic():
            if not lobby_players:
                if guild_id in self.games: del self.games[guild_id] # Cleanup lock if failed
                await msg.edit(content="No players joined. Cancelled.", view=None, embed=None)
                return

            # Create actual game
            game = BlackjackGame(self, interaction.channel, lobby_players)
            self.games[guild_id] = game
            await msg.delete() # Clean up lobby
            await game.start()

        # Wait for view to timeout or stop
        if await view.wait():
            # Timeout
            if not lobby_players:
                await msg.edit(content="Lobby timed out.", view=None)
            else:
                await start_game_logic()
        else:
            # View stopped manually (Start Now)
            pass

class BetModal(discord.ui.Modal, title="Place your Bet"):
    bet_amount = discord.ui.TextInput(label="Amount", placeholder="10", min_length=1, max_length=10)

    def __init__(self, cog, lobby_list, max_p, view, message):
        super().__init__()
        self.cog = cog
        self.lobby = lobby_list
        self.max_p = max_p
        self.view = view
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.bet_amount.value)
        except ValueError:
            return await interaction.response.send_message("Invalid number.", ephemeral=True, delete_after=5)

        bal = self.cog.bank.get(interaction.user.id, (0,))[0]
        if amount < self.cog.minimum_bet:
            return await interaction.response.send_message(f"Min bet is {self.cog.minimum_bet}.", ephemeral=True, delete_after=5)
        if bal < amount:
            return await interaction.response.send_message("Insufficient funds.", ephemeral=True, delete_after=5)

        # Deduct
        self.cog.bank[interaction.user.id] = (bal - amount, self.cog.bank[interaction.user.id][1])
        
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