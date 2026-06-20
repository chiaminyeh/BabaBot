# import pygame
# pygame.init()


# class Screen:
#     def __init__(self, size = (800, 800), title = ""):
#         self.size = size
#         self.sizeX = size[0]
#         self.sizeY = size[1]
        
#         self.screen = pygame.display.set_mode(size, 0)
#         pygame.display.set_caption(title)
#         self.screen.fill((0,0,0))

#         self.background_color = (0, 0, 0)  # Black

#     def draw(self, image, position=(0,0)):
#         self.screen.blit(image, position)

#     def update(self):
#         pygame.display.update()

#     def quit(self):
#         pygame.quit()

#     def clear(self):
#         self.screen.fill((0,0,0))