"""Draws the app icon (a small answer sheet with bubbles, some filled) and writes icon.png / .ico / .icns.
Run once with any Python that has Pillow:   python packaging/icons/make_icons.py"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill=(31, 78, 140, 255))          # background
d.rounded_rectangle([200, 140, S - 200, S - 140], radius=40, fill=(255, 255, 255, 255))     # sheet
for x, y in [(240, 180), (S - 300, 180), (240, S - 240)]:                                 # corner markers
    d.rectangle([x, y, x + 60, y + 60], fill=(20, 20, 20, 255))
filled = {0: 1, 1: 3, 2: 0, 3: 2}
for row in range(4):
    y = 310 + row * 120
    for col in range(4):
        x = 330 + col * 115
        box = [x, y, x + 80, y + 80]
        if filled[row] == col:
            d.ellipse(box, fill=(20, 20, 20, 255))
        else:
            d.ellipse(box, outline=(90, 90, 90, 255), width=10)
img.save(os.path.join(HERE, "icon.png"))
img.resize((512, 512), Image.LANCZOS).save(os.path.join(HERE, "icon-512.png"))   # Linux menus
img.save(os.path.join(HERE, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(os.path.join(HERE, "icon.icns"))
print("written")
