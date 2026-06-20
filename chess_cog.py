# cogs/chess_cog.py
import time
import contextlib
from dataclasses import dataclass
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands
import chess

MOVE_HINT = (
    "Type your move here, e.g. `e4`, `Nf3`, `O-O`, or `e2e4`.\n"
    "You can also type `resign` to resign."
)

HISTORY_MAX_PLIES = 200  # how many half-moves to keep in the displayed history

resign_phrases = {"resign", "gg", "GG", "i resign", "concede", "surrender", "i don't want to play anymore", "i dont wanna play anymore", ""
        "i don't want to play", "i dont wanna play", "i give up", "delete me from the game", "remove me from the game", "game - me", "FF", "forfeit", "ff",
        "I don't wanna play anymore"}


    # ----- YOUR SERVER EMOJIS -----
PIECE = {
    (chess.WHITE, chess.PAWN):   "<:whitepawn:1217276888020549722>",
    (chess.WHITE, chess.KNIGHT): "<:whiteknight:1217276836128624801>",
    (chess.WHITE, chess.BISHOP): "<:whitebishop:1217276833440206970>",
    (chess.WHITE, chess.ROOK):   "<:whiterook:1217276908761255987>",
    (chess.WHITE, chess.QUEEN):  "<:whitequeen:1217276839337263116>",
    (chess.WHITE, chess.KING):   "<:whiteking:1217276834455093330>",
    (chess.BLACK, chess.PAWN):   "<:blackpawn:1217276829358882826>",
    (chess.BLACK, chess.KNIGHT): "<:blackknight:1217276828297990215>",
    (chess.BLACK, chess.BISHOP): "<:blackbishop:1217276824841879674>",
    (chess.BLACK, chess.ROOK):   "<:blackrook:1217276832148226198>",
    (chess.BLACK, chess.QUEEN):  "<:blackqueen:1217276830898323486>",
    (chess.BLACK, chess.KING):   "<:blackking:1217276826510954630>",
}

# ----- WOOD THEME SQUARES -----
LIGHT = "🟨"   # light wood
DARK  = "🟫"   # dark wood
HL_LIGHT = "🟧"  # highlight for last-move square if empty
HL_DARK  = "🟫"

def _status_text(board: chess.Board) -> str:
    if board.is_checkmate():
        winner = "Black" if board.turn == chess.WHITE else "White"
        return f"Checkmate — **{winner} wins!**"
    if board.is_stalemate():
        return "Stalemate — **draw**."
    if board.is_insufficient_material():
        return "Insufficient material — **draw**."
    if board.can_claim_threefold_repetition():
        return "Threefold repetition available — draw claim possible."
    if board.can_claim_fifty_moves():
        return "50-move rule available — draw claim possible."
    side = "White" if board.turn == chess.WHITE else "Black"
    return f"{side} to move" + (" (check!)" if board.is_check() else "") + "."

def render_emoji_board(board: chess.Board, *, flip_for_black_turn: bool = False, show_labels: bool = True) -> str:
    """
    Render the board with custom piece emojis + wood squares.
    If flip_for_black_turn is True and it's Black to move, the board is shown from Black's perspective.
    """
    flip = flip_for_black_turn and board.turn == chess.BLACK

    last = board.peek() if board.move_stack else None
    from_sq = last.from_square if last else None
    to_sq   = last.to_square if last else None

    # orientation
    rank_iter = range(0, 8) if flip else range(7, -1, -1)
    file_iter = range(7, -1, -1) if flip else range(0, 8)

    # file labels
    file_labels = ("     h    g    f    e    d    c    b    a   ") if flip else ("    a    b    c    d    e    f    g    h  ")

    lines = []
    if show_labels:
        lines.append(f"{file_labels}")

    for r in rank_iter:
        row = []
        for f in file_iter:
            sq = chess.square(f, r)
            piece = board.piece_at(sq)
            if piece:
                row.append(PIECE[(piece.color, piece.piece_type)])
            else:
                is_light = (r + f) % 2 == 0
                base = LIGHT if is_light else DARK
                if sq in (from_sq, to_sq):
                    base = HL_LIGHT if is_light else HL_DARK
                row.append(base)
        line = "".join(row)
        if show_labels:
            # rank label should reflect the displayed orientation naturally
            line = f"{r+1} {line} {r+1}"
        lines.append(line)

    if show_labels:
        lines.append(f"{file_labels}")
    return "\n".join(lines)

def format_history(board: chess.Board, max_plies: int = HISTORY_MAX_PLIES) -> str:
    """Return numbered SAN history like `1. e4 e5 2. Nf3 Nc6` (last N plies)."""
    tmp = chess.Board()
    all_sans: List[str] = []
    for mv in board.move_stack:
        all_sans.append(tmp.san(mv))
        tmp.push(mv)

    total = len(all_sans)
    sans = all_sans[-max_plies:] if total > max_plies else all_sans

    start_ply_index = total - len(sans)  # where this slice begins in the full game
    move_no = 1 + (start_ply_index // 2)
    starts_with_black = (start_ply_index % 2 == 1)

    out = []
    i = 0
    if starts_with_black and i < len(sans):
        out.append(f"{move_no}... {sans[i]}")
        i += 1
        move_no += 1

    while i < len(sans):
        if i + 1 < len(sans):
            out.append(f"{move_no}. {sans[i]} {sans[i+1]}")
            i += 2
        else:
            out.append(f"{move_no}. {sans[i]}")
            i += 1
        move_no += 1

    return " ".join(out) if out else "—"

@dataclass
class Game:
    white_id: int
    black_id: int
    started_ts: float
    board: chess.Board
    board_msg_id: Optional[int] = None  # the bot's current board message (we delete & repost)

    def current_player_id(self) -> int:
        return self.white_id if self.board.turn == chess.WHITE else self.black_id

    def is_player(self, uid: int) -> bool:
        return uid in (self.white_id, self.black_id)

class ChessCog(commands.Cog):
    """Play chess by typing moves in the channel (emoji board, wood theme, move history)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: dict[int, Game] = {}  # channel_id -> Game

    chess_group = app_commands.Group(name="chess", description="Play chess in this channel")

    @chess_group.command(name="start", description="Start a chess game here with two players")
    @app_commands.describe(white="White player", black="Black player")
    async def chess_start(self, interaction: discord.Interaction, white: discord.User, black: discord.User):
        cid = interaction.channel_id
        if cid in self.games:
            await interaction.response.send_message("There’s already an active game in this channel. Use `/chess end`.", ephemeral=True)
            return
        if white.id == black.id:
            await interaction.response.send_message("Pick two different users.", ephemeral=True)
            return

        game = Game(white_id=white.id, black_id=black.id, started_ts=time.time(), board=chess.Board())
        self.games[cid] = game

        content = self._compose_message(game, intro=f"**Chess started!**\nWhite: {white.mention}\nBlack: {black.mention}")
        await interaction.response.send_message(content)
        msg = await interaction.original_response()
        self.games[cid].board_msg_id = msg.id

    @chess_group.command(name="end", description="Force-end the current game in this channel")
    async def chess_end(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid not in self.games:
            await interaction.response.send_message("No active game here.", ephemeral=True)
            return
        # best-effort: delete the last board message
        with contextlib.suppress(Exception):
            if self.games[cid].board_msg_id:
                msg = await interaction.channel.fetch_message(self.games[cid].board_msg_id)
                await msg.delete()
        self.games.pop(cid, None)
        await interaction.response.send_message("Game ended and cleared from this channel.")

    @chess_group.command(name="show", description="Show or refresh the board")
    async def chess_show(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        game = self.games.get(cid)
        if not game:
            await interaction.response.send_message("No active game here.", ephemeral=True)
            return
        # Delete the old board message and post a fresh one
        content = self._compose_message(game)
        new_msg = await self._delete_and_post(interaction.channel, game, content)
        await interaction.response.send_message("Board refreshed.", ephemeral=True)
        game.board_msg_id = new_msg.id

    @commands.Cog.listener("on_message")
    async def typed_move(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        game = self.games.get(message.channel.id)
        if not game:
            return
        if not game.is_player(message.author.id):
            return

        content = message.content.strip()
        if not content:
            return

        # resign
        if content.lower() in resign_phrases: # resign phrases
            result_text = f"{message.author.mention} resigns."
            text = self._compose_message(game, outro=result_text, game_over=True)
            new_msg = await self._delete_and_post(message.channel, game, text)
            game.board_msg_id = new_msg.id
            # cleanup game state
            self.games.pop(message.channel.id, None)
            return

        # enforce turn
        if message.author.id != game.current_player_id():
            await message.channel.send(f"It’s not your turn, {message.author.mention}.")
            return

        token = content.split()[0]
        token = token.replace("0-0-0", "O-O-O").replace("0-0", "O-O")  # zeros -> O

        # Try SAN, then UCI
        move_obj = None
        try:
            move_obj = game.board.parse_san(token)
        except Exception:
            try:
                cand = chess.Move.from_uci(token.lower())
                if cand in game.board.legal_moves:
                    move_obj = cand
            except Exception:
                move_obj = None

        if move_obj is None:
            await message.channel.send(f"Invalid/illegal move `{token}`. Try SAN `Nf3`, `O-O` or UCI `e2e4`.")
            return

        # apply move
        game.board.push(move_obj)

        status = _status_text(game.board)
        last_uci = game.board.peek().uci()

        game_over = game.board.is_game_over(claim_draw=True)
        if game_over:
            header = f"**Game over: {game.board.result(claim_draw=True)}**\nLast move: **{last_uci}**\n{status}"
        else:
            header = f"{message.author.mention} played **{last_uci}**.\n{status}"

        text = self._compose_message(game, intro=header, game_over=game_over)
        new_msg = await self._delete_and_post(message.channel, game, text)
        game.board_msg_id = new_msg.id

        if game_over:
            # final cleanup
            self.games.pop(message.channel.id, None)

    # ----- helpers -----
    def _compose_message(self, game: Game, intro: Optional[str] = None, outro: Optional[str] = None, game_over: bool = False) -> str:
        # Flip the board when it's Black to move
        board_text = render_emoji_board(game.board, flip_for_black_turn=True)
        hist = format_history(game.board, HISTORY_MAX_PLIES)

        parts = []
        if intro:
            parts.append(intro)
        else:
            parts.append(_status_text(game.board))
        parts.append("")
        parts.append(board_text)
        parts.append("")
        parts.append(f"**Moves:** {hist}")
        if not game_over:
            parts.append("")
            parts.append(MOVE_HINT)
        if outro:
            parts.append("")
            parts.append(outro)
        return "\n".join(parts)

    async def _delete_and_post(self, channel: discord.TextChannel, game: Game, content: str) -> discord.Message:
        """Delete the bot's previous board message (if any) and post a fresh one."""
        with contextlib.suppress(Exception):
            if game.board_msg_id:
                old = await channel.fetch_message(game.board_msg_id)
                if old.author == channel.guild.me:
                    await old.delete()
                else:
                    # if somehow points to a non-bot message, just ignore deletion
                    pass
        return await channel.send(content)

async def setup(bot: commands.Bot):
    await bot.add_cog(ChessCog(bot))
