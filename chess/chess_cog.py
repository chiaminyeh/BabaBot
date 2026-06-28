import discord
from discord import app_commands
from discord.ext import commands
import re



class Piece:
    def __init__(self,name='',color='',x=-1,y=-1,point=0,image=""):
        self.name = name
        self.color = color
        self.x = int(x)
        self.y = int(y)
        self.chessX = x
        self.chessY = 9 - y

        self.point = point
        self.image = image

    def move(self, new_x, new_y):
        self.x = new_x
        self.y = new_y

    def remove(self):
        self.name = ""
        self.color = ""



class Pawn(Piece):
    def __init__(self, color, x, y):
        super().__init__('Pawn', color, x, y)
        self.point = 1
        self.two_move = False
        self.en_passanting = False
        if(color == "white"):
            self.image = "<:whitepawn:1217276888020549722>" + " "
        else:
            self.image = "<:blackpawn:1217276829358882826>" + " "

    def can_move(self,board,target):
        # print(self.x,self.y)
        # Determine direction based on the piece's color
        direction = 1 if self.color == "white" else -1
        # print(direction)

        # Check if the new position is one step forward and if is blocked by pieces
        if target.chessX == self.chessX and target.chessY == self.chessY + direction and target.name == "":
            return True

        # Check if the new position is two steps forward (allowed only from the starting rank)
        if target.chessX == self.chessX and target.chessY == self.chessY + 2 * direction and self.chessY == (2 if self.color == "white" else 7):
            if target.name == "" and board.grid[self.y-(1*direction)][self.x].name == "":
                self.two_move = True
                return True

        # Check if the new position is a diagonal capture
        if abs(target.chessX - self.chessX) == 1 and target.chessY == self.chessY + direction:
            if target.name != '' and target.color != self.color:
                return True
        # en pessant
            target_piece = board.grid[target.y+direction][target.x]
            if target_piece.name == "Pawn":
                if target_piece.color != self.color:
                    if target_piece.two_move == True:
                        self.en_passanting = True
                        return True
                
        # print('reached the bottom')
        return False


class Knight(Piece):
    def __init__(self, color, x, y):
        super().__init__('Knight', color, x, y)
        self.point = 3
        if(color == "white"):
            self.image = "<:whiteknight:1217276836128624801> "
        else:
            self.image = "<:blackknight:1217276828297990215> "
        
    def can_move(self,board,target):
        # Calculate the absolute difference in x and y coordinates
        dx = abs(target.chessX - self.chessX)
        dy = abs(target.chessY - self.chessY)

        # Check if the knight moves in an L-shape (2 squares in one direction, 1 in another)
        if (dx == 1 and dy == 2) or (dx == 2 and dy == 1):
            if target.color != self.color:
                return True
        
        return False

class Bishop(Piece):
    def __init__(self, color, x, y):
        super().__init__('Bishop', color, x, y)
        self.point = 3
        if(color == "white"):
            self.image = "<:whitebishop:1217276833440206970> "
        else:
            self.image = "<:blackbishop:1217276824841879674> "

    def can_move(self,board,target):
        # Calculate the absolute differences in x and y coordinates
        dx = self.chessX - target.chessX
        dy = target.chessY - self.chessY

        # Check if the bishop moves diagonally (dx == dy)
        if abs(dx) == abs(dy):
            # Determine direction of movement
            dx_sign = 1 if dx > 0 else -1
            dy_sign = 1 if dy > 0 else -1

            # determine if has obstacles
            for i in range(1,abs(dx)):
                if board.grid[self.y - i*dy_sign][self.x - i*dx_sign].name != "":
                    return False

            if target.name == '' or target.color != self.color:
                return True

        return False



class Rook(Piece):
    def __init__(self, color, x, y):
        super().__init__('Rook', color, x, y)
        self.point = 1
        if(color == "white"):
            self.image = "<:whiterook:1217276908761255987> "
        else:
            self.image = "<:blackrook:1217276832148226198> "

    def can_move(self,board,target):
        # Calculate the absolute differences in x and y coordinates
        dx = target.chessX - self.chessX
        dy = target.chessY - self.chessY

        # Check if the rook moves horizontally (dy == 0) or vertically (dx == 0)
        if dx == 0:
            # Vertical movement
            dy_sign = 1 if dy > 0 else -1
            for i in range(1, abs(dy)):
                if board.grid[self.y- i * dy_sign][self.x].name != '':
                    return False
        elif dy == 0:
            # Horizontal movement
            dy_sign = 1 if dx > 0 else -1
            for i in range(1, abs(dx)):
                if board.grid[self.y][self.x + i * dy_sign].name != '':
                    return False
        else:
            return False  # Rook cannot move diagonally

        if target.name == '' or target.color != self.color:

            return True
        
        return False


class Queen(Piece):
    def __init__(self, color, x, y):
        super().__init__('Queen', color, x, y)
        self.point = 1
        if(color == "white"):
            self.image = "<:whitequeen:1217276839337263116> "
        else:
            self.image = "<:blackqueen:1217276830898323486> "

    def can_move(self,board,target):
    
        dx = target.chessX - self.chessX
        dy = target.chessY - self.chessY

        if dx == 0:
            # Vertical movement
            dy_sign = 1 if dy > 0 else -1
            for i in range(1, abs(dy)):
                if board.grid[self.y- i * dy_sign][self.x].name != '':
                    return False
        elif dy == 0:
            # Horizontal movement
            dx_sign = 1 if dx > 0 else -1
            for i in range(1, abs(dx)):
                if board.grid[self.y][self.x + i * dx_sign].name != '':
                    return False
        elif abs(dx) == abs(dy):
            # Determine direction of movement
            dx_sign = 1 if dx > 0 else -1
            dy_sign = 1 if dy > 0 else -1

            # determine if has obstacles
            for i in range(1,abs(dx)):
                if board.grid[self.y - i*dy_sign][self.x + i*dx_sign].name != "":
                    return False
        
            if target.name == '' or target.color != self.color:
                return True
        else:
            return False  # Queen cannot move in any other pattern

        if target.name == '' or target.color != self.color:
            return True
        
        return False




class King(Piece):
    def __init__(self, color, x, y):
        super().__init__('King', color, x, y)
        self.can_castle_K = False
        self.can_castle_Q = False
        self.point = 99
        if(color == "white"):
            self.image = "<:whiteking:1217276834455093330> "
        else:
            self.image = "<:blackking:1217276826510954630> "

    def can_move(self,board,target):
        dx = target.chessX - self.chessX
        dy = target.chessY - self.chessY

        # Check if the king moves one square in any direction
        if abs(dx) <= 1 and abs(dy) <= 1:
            if target.name == '' or target.color != self.color:
                return True

        if target.name == "Rook":
            # Check if the king is in the correct starting position for castling
            if dy == 0 and self.chessY == (1 if self.color == "white" else 8):
                # King side castling
                if target.chessX == 8:
                    # Check obstacles between king and rook
                    for i in range(self.chessX+1, 8):
                        if board.grid[self.y][i].name != "":
                            return False
                    # Check if squares the king passes through are not under attack
                    if self.color == "white":
                        if board.is_attacked(6, 1) or board.is_attacked(7,1):
                            return False
                    else:
                        if board.is_attacked(6, 8) or board.is_attacked(7,8):
                            return False
                    # If all conditions are met, allow castling
                    self.can_castle_K = True
                    return True
                # Queen side castling
                elif target.chessX == 1:
                    # Check obstacles between king and rook
                    for i in range(self.chessX-1, 1, -1):
                        if board.grid[self.y][i].name != "":
                            return False
                    # Check if squares the king passes through are not under attack
                    if self.color == "white":
                        if board.is_attacked(3, 1) or board.is_attacked(4,1):
                            return False
                    else:
                        if board.is_attacked(3,8) or board.is_attacked(4,8):
                            return False
                    # If all conditions are met, allow castling
                    self.can_castle_Q = True
                    return True

        return False
    
    def castle(self,board,target):
        
        if(self.can_castle_K == True and target.chessX == 8):
            board.super_move(target,target.x-2,target.y)
            board.super_move(self,target.x-1,target.y)
            self.can_castle_K = False
            self.can_castle_Q = False
        elif(self.can_castle_Q == True and target.chessX == 1):
            board.super_move(target,target.x +3,target.y)
            board.super_move(self,target.x +2,target.y)
            self.can_castle_K = False
            self.can_castle_Q = False

        return

        




# class board():
#     def __init__(self):
#         self.scene = []
#         self.grid = [[Piece("","","Z","0",)] * 8 for _ in range(8)]
#         self.started_games = {}  # Dictionary to track members who have started a game
#         self.turn = "white"
#         self.convert = {"a":8,"b":7,"c":6,"d":5,"e":4,"f":3,"g":2,"h":1}

#         # add the Pieces
#         self.scene.append(Pawn("white","a","2"))
#         self.scene.append(Pawn("white","b","2"))
#         self.scene.append(Pawn("white","c","2"))
#         self.scene.append(Pawn("white","d","2"))
#         self.scene.append(Pawn("white","e","2"))
#         self.scene.append(Pawn("white","f","2"))
#         self.scene.append(Pawn("white","g","2"))
#         self.scene.append(Pawn("white","h","2"))
#         self.scene.append(Pawn("black","h","7"))
#         self.scene.append(Pawn("black","g","7"))
#         self.scene.append(Pawn("black","f","7"))
#         self.scene.append(Pawn("black","e","7"))
#         self.scene.append(Pawn("black","d","7"))
#         self.scene.append(Pawn("black","c","7"))
#         self.scene.append(Pawn("black","b","7"))
#         self.scene.append(Pawn("black","a","7"))
#         self.scene.append(Rook("white", "a", "1"))
#         self.scene.append(Knight("white", "b", "1"))
#         self.scene.append(Bishop("white", "c", "1"))
#         self.scene.append(Queen("white", "e", "1"))
#         self.scene.append(King("white", "d", "1"))
#         self.scene.append(Bishop("white", "f", "1"))
#         self.scene.append(Knight("white", "g", "1"))
#         self.scene.append(Rook("white", "h", "1"))
#         self.scene.append(Rook("black", "a", "8"))
#         self.scene.append(Knight("black", "b", "8"))
#         self.scene.append(Bishop("black", "c", "8"))
#         self.scene.append(Queen("black", "e", "8"))
#         self.scene.append(King("black", "d", "8"))
#         self.scene.append(Bishop("black", "f", "8"))
#         self.scene.append(Knight("black", "g", "8"))
#         self.scene.append(Rook("black", "h", "8"))

    
#         for obj in self.scene:
#             self.grid[self.convert[obj.x]-1][int(obj.y)-1] = (obj)

#     def EmbedGenerate(self):
#         Embed = discord.Embed(title="Chess", description = self.print_board())
#         # Embed.set_footer(text="Score: " + str(score))
#         return Embed
#         # await interaction.response.edit_message(embed=EmbedGenerate())

#     def print_board(self):
#         self.grid = [[Piece("","","Z","0",)] * 8 for _ in range(8)]
#         for obj in self.scene:
#             self.grid[self.convert[obj.x]-1][int(obj.y)-1] = (obj)
        
#         pboard = ""
#         # reverse so white is down
#         for i in reversed(range(8)):
#             for j in reversed(range(8)):
#                 if self.grid[j][i].name!="":
#                     pboard += self.grid[j][i].image
#                 else:
#                     pboard += ":green_square:" + " "
#             pboard+="\n"
#         return pboard

#     async def move(self,ctx,move_command):
#         # Pawn move format: e5
#         pawn_match = re.match(r'([a-h])([1-8])', move_command)
#         # Other piece move format: Ne4, Bg5, etc.
#         piece_match = re.match(r'([NBRQK])([a-h])([1-8])', move_command)

#         if pawn_match:
#             print("pawn matched")
#             new_x = board.convert[pawn_match.group(1)]
#             new_y = pawn_match.group(2)
#             # Convert new_x and new_y to integers for comparison
#             new_y = int(new_y)
#             for piece in board.scene:
#                 if piece.name.lower() == 'pawn' and self.can_move_to(board,piece, new_x, new_y):
#                     if self.is_capture(board, piece, new_x, new_y):
#                         # Remove the captured piece from the board
#                         self.remove_piece(board, new_x, new_y)
#                     piece.move(new_x, new_y)
#                     board.turn = self.change_turn(board)
#                     await ctx.send(self.boards[ctx.author.id].print_board())
#                     return
#             await ctx.send("No pawn can move to that position!")

#         elif piece_match:
#             print("piece matched")
#             piece_name = piece_match.group(1)
#             new_x = board.convert[piece_match.group(2)]
#             new_y = piece_match.group(3)
#             # Convert new_x and new_y to integers for comparison
#             new_y = int(new_y)
#             for piece in board.scene:
#                 if piece.name.lower() == piece_name.lower() and self.can_move_to(board,piece, new_x, new_y):
#                     if self.is_capture(board, piece, new_x, new_y):
#                     # Remove the captured piece from the board
#                         self.remove_piece(board, new_x, new_y)
#                     piece.move(new_x, new_y)
#                     board.turn = self.change_turn(board)
#                     await ctx.send(self.boards[ctx.author.id].print_board())
#                     return
#             await ctx.send(f"No {piece_name} can move to that position!")

#         else:
#             await ctx.send("Invalid move format! Example: `!move Ne4` or `!move e5`")
#         # Check for check condition
#         if await self.is_check(ctx,board.turn):
#             await ctx.send("Check!")

#         # Check for checkmate condition
#         if await self.is_checkmate(ctx):
#             await ctx.send("Checkmate! Game over.")
#             # Close the game for the player
#             del self.boards[ctx.author.id]
#             return
#     # end of move

#     def is_capture(self, board, piece, new_x, new_y):
#         target_piece = board.grid[new_x-1][int(new_y) - 1]
#         return target_piece.name != '' and target_piece.color != piece.color

#     def remove_piece(self, board, x, y):
#         board.grid[x-1][int(y) - 1] = Piece("", "", "Z", "0")
    
#     def change_turn(self,board):
#         if board.turn == "white":
#             return "black" 
#         else:
#             return "white"

#     def can_move_to(self, board, piece, new_x, new_y):
#         if not piece.color == board.turn:
#             return False

#         # Check if the new position is within the board boundaries
#         if not (1 <= new_x <= 8 and 1 <= new_y <= 8):
#             return False

#         if piece.name == 'Pawn':
#             # Determine direction based on the piece's color
#             direction = 1 if piece.color == "white" else -1

#             # Check if the new position is one step forward
#             if new_x == piece.x and new_y == piece.y + direction and board.grid[new_x - 1][new_y - 1].name == "":
#                 return True

#             # Check if the new position is two steps forward (allowed only from the starting rank)
#             if (new_x == piece.x and new_y == piece.y + 2 * direction and piece.y == (2 if piece.color == "white" else 7) and board.grid[new_x - 1][new_y - 1].name == "" and board.grid[new_x - 1][piece.y + direction - 1].name == ""):
#                 return True

#             # Check if the new position is a diagonal capture
#             if abs(new_x - piece.x) == 1 and new_y == piece.y + direction:
#                 target_piece = board.grid[new_x - 1][new_y - 1]
#                 if target_piece.name != '' and target_piece.color != piece.color:
#                     return True

#         elif piece.name == 'N':
#             # Calculate the absolute difference in x and y coordinates
#             dx = abs(new_x - piece.x)
#             dy = abs(new_y - piece.y)

#             # Check if the knight moves in an L-shape (2 squares in one direction, 1 in another)
#             if (dx == 1 and dy == 2) or (dx == 2 and dy == 1):
#                 target_piece = board.grid[new_x - 1][new_y - 1]
#                 if target_piece.name == '' or target_piece.color != piece.color:
#                     return True

#         elif piece.name == "B":
#             # Calculate the absolute differences in x and y coordinates
#             dx = new_x - piece.x
#             dy = new_y - piece.y

#             # Check if the bishop moves diagonally (dx == dy)
#             if abs(dx) == abs(dy):
#                 # Determine direction of movement
#                 dx_sign = 1 if dx > 0 else -1
#                 dy_sign = 1 if dy > 0 else -1

#                 # Check for obstructions along the diagonal path
#                 for i in range(1, abs(dx)):
#                     if board.grid[piece.x + i * dx_sign - 1][piece.y + i * dy_sign - 1].name != '':
#                         return False
#                 target_piece = board.grid[new_x - 1][new_y - 1]
#                 if target_piece.name == '' or target_piece.color != piece.color:
#                     return True

#         elif piece.name == "R":
#             # Calculate the absolute differences in x and y coordinates
#             dx = new_x - piece.x
#             dy = new_y - piece.y

#             # Check if the rook moves horizontally (dy == 0) or vertically (dx == 0)
#             if dx == 0:
#                 # Vertical movement
#                 dy_sign = 1 if dy > 0 else -1
#                 for i in range(1, abs(dy)):
#                     if board.grid[piece.x - 1][piece.y + i * dy_sign - 1].name != '':
#                         return False
#             elif dy == 0:
#                 # Horizontal movement
#                 dx_sign = 1 if dx > 0 else -1
#                 for i in range(1, abs(dx)):
#                     if board.grid[piece.x + i * dx_sign - 1][piece.y - 1].name != '':
#                         return False
#             else:
#                 return False  # Rook cannot move diagonally

#             target_piece = board.grid[new_x - 1][new_y - 1]
#             if target_piece.name == '' or target_piece.color != piece.color:
#                 return True

#         elif piece.name == "Q":
#             dx = new_x - piece.x
#             dy = new_y - piece.y

#             if dx == 0:
#                 # Vertical movement
#                 dy_sign = 1 if dy > 0 else -1
#                 for i in range(1, abs(dy)):
#                     if board.grid[piece.x - 1][piece.y + i * dy_sign - 1].name != '':
#                         return False
#             elif dy == 0:
#                 # Horizontal movement
#                 dx_sign = 1 if dx > 0 else -1
#                 for i in range(1, abs(dx)):
#                     if board.grid[piece.x + i * dx_sign - 1][piece.y - 1].name != '':
#                         return False
#             elif abs(dx) == abs(dy):
#                 # Diagonal movement
#                 dx_sign = 1 if dx > 0 else -1
#                 dy_sign = 1 if dy > 0 else -1
#                 for i in range(1, abs(dx)):
#                     if board.grid[piece.x + i * dx_sign - 1][piece.y + i * dy_sign - 1].name != '':
#                         return False
#             else:
#                 return False  # Queen cannot move in any other pattern

#             target_piece = board.grid[new_x - 1][new_y - 1]
#             if target_piece.name == '' or target_piece.color != piece.color:
#                 return True

#         elif piece.name == "K":
#             dx = new_x - piece.x
#             dy = new_y - piece.y

#             # Check if the king moves one square in any direction
#             if abs(dx) <= 1 and abs(dy) <= 1:
#                 target_piece = board.grid[new_x - 1][new_y - 1]
#                 if target_piece.name == '' or target_piece.color != piece.color:
#                     return True

#         return False


#     def is_checkmate(self, ctx):
#         board = self.boards[ctx.author.id]
#         color = board.turn

#         # Find the king of the current player
#         king = None
#         for piece in board.scene:
#             if piece.name == 'K' and piece.color == color:
#                 king = piece
#                 break

#         # Check if the king is in check
#         if not self.is_check(board, color):
#             return False

#         # Check if there are any legal moves the player can make to get out of check
#         for piece in board.scene:
#             if piece.color == color:
#                 for x in range(1, 9):
#                     for y in range(1, 9):
#                         if self.can_move_to(board, piece, x, y):
#                             # Make a hypothetical move and check if the king is still in check
#                             original_x, original_y = piece.x, piece.y
#                             target_piece = board.grid[x - 1][y - 1]
#                             piece.move(x, y)
#                             if not self.is_check(board, color):
#                                 # Undo the hypothetical move
#                                 piece.move(original_x, original_y)
#                                 board.grid[x - 1][y - 1] = target_piece
#                                 return False
#                             # Undo the hypothetical move
#                             piece.move(original_x, original_y)
#                             board.grid[x - 1][y - 1] = target_piece
#         return True

#     def is_check(self, board, color):
#         # Find the king of the specified color
#         for piece in board.scene:
#             if piece.color == color and piece.name == 'K':
#                 king_x, king_y = piece.x, piece.y
#                 break

#         # Check if any opponent's piece can move to the king's position
#         for piece in board.scene:
#             if piece.color != color and self.can_move_to(board, piece, king_x, king_y):
#                 return True
#         return False





    


# class ChessCog(commands.Cog):
#     def __init__(self, bot):
#         self.bot = bot
#         self.boards = {}

#     @commands.command(name="start", help="Start a chess game")
#     async def start_game(self, ctx):
#         # Check if the player has already started a game
#         if ctx.author.id in self.boards:
#             await ctx.send("You've already started a game!")
#             return

#         # Create a new board for the player
#         self.boards[ctx.author.id] = board()

#         await ctx.send(self.boards[ctx.author.id].print_board())
    
#     @commands.command(name="move", help="Move a chess piece")
#     async def move_piece(self, ctx, move_command: str):
#         if ctx.author.id not in self.boards:
#             await ctx.send("You haven't started a game yet!")
#             return

#         board = self.boards[ctx.author.id]

#         # board.move("e4")
#         await ctx.send(self.boards[ctx.author.id].print_board())

#     @commands.command(name="testing")
#     async def testing(self, ctx):
#         board = self.boards[ctx.author.id]
#         print(board.turn)
#         print(len(board.scene))

#     @commands.command(name="close", help="Close the ongoing game")
#     async def close_game(self, ctx):
#         # Check if the player has started a game
#         if ctx.author.id in self.boards:
#             # Close the game for the player
#             del self.boards[ctx.author.id]
#             await ctx.send("Game closed.")
#         else:
#             await ctx.send("You haven't started a game yet.")




# async def setup(bot):
#     await bot.add_cog(ChessCog(bot))
#     try:
#         synced = await bot.tree.sync()
#         print(f"synce {len(synced)} command(s)!")
#     except Exception as e:
#         print(e)
#     print('chess loaded!')

