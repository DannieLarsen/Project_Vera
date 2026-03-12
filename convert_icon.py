from PIL import Image
import os

src = "Logo/New V logo.png"
dst = "Logo/icon.ico"

img = Image.open(src).convert("RGBA")

# Clean up near-transparent pixels for crisp taskbar rendering
pixels = img.load()
for y in range(img.height):
    for x in range(img.width):
        r, g, b, a = pixels[x, y]
        if a < 10:
            pixels[x, y] = (0, 0, 0, 0)

# All standard Windows icon sizes for sharp rendering at every DPI scale
img.save(dst, format="ICO", sizes=[
    (16, 16), (24, 24), (32, 32), (48, 48),
    (64, 64), (128, 128), (256, 256),
])
print(f"icon.ico created from '{src}' — {os.path.getsize(dst)//1024}KB")
