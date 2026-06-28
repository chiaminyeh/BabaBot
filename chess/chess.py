import pygame
from screenHandler import*
import chess_cog as cc
import math
import copy

PSIZE=80

size = (PSIZE*10, PSIZE*10)
screen = Screen(size)


class Board:
    def __init__(self, fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"):
        self.grid = fen_to_grid(fen)
        self.full_moves = 0
        self.half_moves = 0
        self.white_turn = True
        self.turn = "white"
        self.en_passant = False


    def print_board(self):
        print("\n\n")
        for i in self.grid:
            for j in i:
                if(j == 0 or j.name == ""):
                    print(f"{' ':^7}", end='')
                    continue

                if(j.color == "white"): print(f"{(((j.__class__).__name__).upper()):^7}", end='')
                else: print(f"{(((j.__class__).__name__).lower()):^7}", end='')
            print("\n")
        print("\n\n")
    
    def super_move(self,previous,targetX,targetY):
        moving_piece = copy.copy(previous)
        previous.remove()
        self.grid[targetY].remove(self.grid[targetY][targetX])
        self.grid[targetY].insert(targetX,moving_piece)
        self.update_piece(moving_piece,targetX,targetY)
   

    
    def move(self,previous,targetX,targetY):

        # print('targetX:',targetX,'targetY:',targetY)

        moving_piece = copy.copy(previous)
        if(self.can_move_to(moving_piece,targetX,targetY)):

            #castle
            if moving_piece.name == "King" and self.grid[targetY][targetX].name == "Rook":
                moving_piece.castle(self,self.grid[targetY][targetX])
                previous.remove()
                return True 
            # normal
            else:
                previous.remove()
                # en passant
                if moving_piece.name == "Pawn":
                    if moving_piece.en_passanting == True:
                        direction = 1 if moving_piece.color == "white" else -1
                        target_piece = self.grid[targetY + direction][targetX]
                        if target_piece.name == "Pawn":
                            target_piece.remove()

                self.grid[targetY].remove(self.grid[targetY][targetX])
                self.grid[targetY].insert(targetX,moving_piece)
                self.update_piece(moving_piece,targetX,targetY)
                return True
            

            # check for checkmate
            
            
        else:
            print('INVALID MOVE')
            return False
        
    # end of move
        pass
    
    def update_piece(self,piece,x,y):
        piece.x = x
        piece.y = y
        piece.chessX = x
        piece.chessY = 9 - y

    def can_move_to(self,piece, new_x, new_y):
        target = self.grid[new_y][new_x]

        # reposition
        target.chessX = new_x
        target.chessY = 9 - new_y
        # print('chessx:',piece.chessX,'chessy',piece.chessY)
        # print('new x:',new_x,'new y:', new_y)

        if not piece.color == self.turn:
            print('wrong color')
            return False
        
        # Check if the new position is within the board boundaries
        if not (1 <= target.chessX <= 8 and 1 <= target.chessY <= 8):
            print('not from 1-8')
            return False        

        # return piece.can_move(self,target)
        if(piece.can_move(self,target)):
            # Create a new board instance to simulate the move
            simulated_board = copy.deepcopy(self)

            # Simulate the move by updating the piece's position on the simulated board
            simulated_piece = simulated_board.grid[piece.y][piece.x]
            simulated_board.update_piece(simulated_piece,new_x,new_y)

            # Update the board grid with the simulated piece movement
            simulated_board.grid[new_y][new_x] = simulated_piece
            simulated_board.grid[piece.y][piece.x] = cc.Piece()

            # Check if the moving player's king is in check after the move
            if simulated_board.is_check():
                return False
            else:
                #  extra checks

                self.castle = False
                return True

    def is_attacked(self, x, y):
        # Iterate over all pieces on the board
        target = self.grid[9-y][x]
        
        for i in range(1, 9):
            for j in range(1, 9):
                piece = self.grid[i][j]
                if piece.name == "":
                    continue
                # Check if the piece can move to the target square (x, y)
                if piece.can_move(self,target) and piece.color!=self.turn:
                    print('true test',i,j,x,y)
                    return True
                
        return False


    def is_check(self):
        # Find the king of the current player
        king_position = None
        for i in range(1, 9):
            for j in range(1, 9):
                piece = self.grid[i][j]
                if piece.name == 'King' and piece.color == self.turn:
                    king_position = (i, j)
                    break
            if king_position:
                break

        if not king_position:
            return False  # Couldn't find the king

        # Check if any opponent's piece can attack the king
        for i in range(1, 9):
            for j in range(1, 9):
                piece = self.grid[i][j]
                if piece.name != '' and piece.color != self.turn:
                    if piece.can_move(self, self.grid[king_position[0]][king_position[1]]):
                        return True

        return False



def draw_board(board, screen):
    for i in range(1,9):
        for j in range(1,9):
                if((i + j) % 2):
                    pygame.draw.rect(screen, (119,149,86), pygame.Rect(i*PSIZE, j*PSIZE,PSIZE,PSIZE))
                else:
                    pygame.draw.rect(screen, (235,236,208), pygame.Rect(i*PSIZE, j*PSIZE,PSIZE,PSIZE))
                piece = board.grid[j][i]
                if(piece == 0):
                    continue
                if piece.name == "":
                    continue
                filename = "Sprites/"
                if (piece.color == "white"):
                    filename += "white-"
                else:
                    filename += "black-"
                filename += ((piece.__class__).__name__).lower() + ".png"
                image = pygame.image.load(filename)
                image = pygame.transform.scale(image, (PSIZE, PSIZE))

                screen.blit(image, (i * PSIZE, j * PSIZE))


def fen_to_grid(fen):
    # Creating a new 2d array
    grid = [[cc.Piece() for _ in range(10)] for _ in range(10)]

    col = 1
    row = 1
    # "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
    # Setting up the board with the pieces
    for i in fen:
        if(i == '/'):
            row = 1
            col += 1
            continue
        if(i == ' '):
            break
        if(i.isnumeric()):
            for j in range(int(i)):
                grid[col][row].x = row
                grid[col][row].y = int(col)
                row += 1
            continue
        if i.isupper():
            color = "white"
        else:
            color = "black"

        if(i.lower() == "p"):
            grid[col][row]=(cc.Pawn(color,row, col))
        elif(i.lower() == "n"):
            grid[col][row]=(cc.Knight(color,row, col))
        elif(i.lower() == "b"):
            grid[col][row]=(cc.Bishop(color,row, col))
        elif(i.lower() == "r"):
            grid[col][row]=(cc.Rook(color,row, col))
        elif(i.lower() == "q"):
            grid[col][row]=(cc.Queen(color,row, col))
        elif(i.lower() == "k"):
            grid[col][row]=(cc.King(color,row, col))
        
        
        print(f"Successfully added a {(grid[col][row]).__class__.__name__} at {row}, {col}")
        row += 1
        
    return grid



def main():

    board1 = Board()
    # board1 = Board("r1bqk2r/ppp2ppp/2np1n2/2b1p1B1/2B1P3/3P1N2/PPPQ1PPP/RN2K2R b KQkq - 3 6")

    # board1.print_board()
    # board2.print_board()

    screen = pygame.display.set_mode(size, 0)

    running = True
    chosen = cc.Piece()
    selected = False

    screen.fill(50)
    draw_board(board1, screen)


    while(running):
        # mouse area
        mouseX,mouseY = pygame.mouse.get_pos()
        # mouseX = math.floor((mouseX)/PSIZE)*PSIZE
        # mouseY = math.floor((mouseY)/PSIZE)*PSIZE
        # pygame.draw.circle(screen, (255,0,0), (mouseX+PSIZE/2,mouseY+PSIZE/2), PSIZE/2, 2)
        mouseX = int(mouseX/PSIZE)
        mouseY = int(mouseY/PSIZE)
        
         

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_q:
                    running = False
            if event.type == pygame.MOUSEBUTTONDOWN:
                if(pygame.mouse.get_pressed()[0]):
                    print(mouseX,mouseY)
                    if not selected:
                        obj = board1.grid[mouseY][mouseX]
                        if (obj.name !="" and obj.color == board1.turn):
                            print("selected " + obj.name)
                            chosen = (obj)
                            selected = True

                            # check for available spots
                            draw_board(board1, screen)
                            for i in range(1,9):
                                for j in range(1,9):
                                    spot = board1.grid[i][j]
                                    if board1.can_move_to(chosen, spot.x, spot.y):
                                        pygame.draw.circle(screen, (235,20,20), (spot.x*PSIZE+PSIZE/2, spot.y*PSIZE+PSIZE/2), 10)

                    else:
                        print('mousex:',mouseX,'mousey:',mouseY,chosen.x,chosen.y)
                        if not (mouseX == chosen.x and mouseY == chosen.y):
                                                     
                            if(board1.move(chosen,mouseX,mouseY)):
                                board1.turn = ("black" if board1.turn == 'white' else 'white')
                            
                        else:
                            print('same position')
                        selected = False

                        draw_board(board1, screen)
                        pass
        

        pygame.display.update()

if (__name__ == "__main__"):
    main()