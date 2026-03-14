"""
Generate Logo/bitdog.ico — multi-resolution ICO with the BitDog front-face head.
Run once before building:  python make_icon.py
"""
import sys, io, os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QPainter, QColor, QPainterPath
from PySide6.QtCore import Qt, QByteArray, QBuffer
from PIL import Image

# ── Same palette as app.py ────────────────────────────────────────────────────
ACCENT   = "#e8660a"
BG_PANEL = "#242424"

_SPRITE = [
    [0,1,1,0,0,0,0,0,0,0,1,1,0],
    [0,1,1,0,0,0,0,0,0,0,1,1,0],
    [0,1,1,1,1,1,1,1,1,1,1,1,0],
    [0,0,1,1,1,1,1,1,1,1,1,0,0],
    [0,0,1,3,1,1,1,1,1,3,1,0,0],
    [0,0,1,1,1,1,1,1,1,1,1,0,0],
    [0,0,1,1,1,2,2,2,1,1,1,0,0],
    [0,0,0,1,1,1,1,1,1,1,0,0,0],
    [0,0,0,0,1,1,1,1,1,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0,0],
]
W, H = 13, 10


def render(size: int) -> QPixmap:
    col_body = QColor(ACCENT)
    col_dark = QColor(ACCENT).darker(180)
    col_eye  = QColor("#d8d8d8")
    col_bg   = QColor(BG_PANEL)

    cell    = max(1, (size * 78 // 100) // W)
    sw      = W * cell
    sh      = H * cell
    x_off   = (size - sw) // 2
    y_off   = (size - sh) // 2

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    radius = max(2, size * 22 // 100)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    p.fillPath(path, col_bg)

    for r, row in enumerate(_SPRITE):
        for c, v in enumerate(row):
            if v == 0:
                continue
            color = col_body if v == 1 else col_dark if v == 2 else col_eye
            p.fillRect(x_off + c * cell, y_off + r * cell, cell, cell, color)
    p.end()
    return pix


def pix_to_pil(pix: QPixmap) -> Image.Image:
    ba  = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pix.save(buf, "PNG")
    buf.close()
    return Image.open(io.BytesIO(bytes(ba))).convert("RGBA")


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [pix_to_pil(render(s)) for s in sizes]

    out = os.path.join(os.path.dirname(__file__), "Logo", "bitdog.ico")
    # Pillow saves multi-resolution ICO when given a list via append_images
    images[0].save(
        out, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )
    print(f"Saved {out}  ({len(sizes)} sizes: {sizes})")
