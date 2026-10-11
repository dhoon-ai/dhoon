"""Draws assets/assistant-avatar.png, the Assistant agent's picture (original artwork, MIT).

Run:  python3 assets/make_avatar.py   (needs Pillow: pip install pillow)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 4
W = 512 * S
img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

# gradient disc: deep indigo -> teal (diagonal)
grad = Image.new("RGBA", (W, W))
gp = grad.load()
a, b = (49, 46, 129), (13, 148, 136)
for y in range(W):
    for x in range(0, W):
        t = (x + y) / (2 * W)
        gp[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)
mask = Image.new("L", (W, W), 0)
ImageDraw.Draw(mask).ellipse((0, 0, W - 1, W - 1), fill=255)
img.paste(grad, (0, 0), mask)
d = ImageDraw.Draw(img)

# subtle circuit traces in the background
trace = (255, 255, 255, 38)
lw = 6 * S
for x0, y0, x1, y1, x2, y2 in [
    (70, 300, 120, 300, 150, 270),
    (442, 300, 392, 300, 362, 270),
]:
    pts = [(x0 * S, y0 * S), (x1 * S, y1 * S), (x2 * S, y2 * S)]
    d.line(pts, fill=trace, width=lw, joint="curve")
    r = 9 * S
    d.ellipse((x0 * S - r, y0 * S - r, x0 * S + r, y0 * S + r), fill=trace)

# antenna + glowing spark
cx = 256 * S
d.line((cx, 150 * S, cx, 105 * S), fill=(255, 255, 255, 255), width=12 * S)
glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
r = 46 * S
gd.ellipse((cx - r, 92 * S - r, cx + r, 92 * S + r), fill=(253, 224, 71, 150))
glow = glow.filter(ImageFilter.GaussianBlur(18 * S))
img.alpha_composite(glow)
d = ImageDraw.Draw(img)
r = 22 * S
d.ellipse((cx - r, 92 * S - r, cx + r, 92 * S + r), fill=(253, 224, 71, 255))

# head
d.rounded_rectangle((130 * S, 150 * S, 382 * S, 360 * S), radius=64 * S, fill=(248, 250, 252, 255))
# ears
for x in (112, 400):
    d.rounded_rectangle(((x - 18) * S, 220 * S, (x + 18) * S, 290 * S), radius=14 * S, fill=(226, 232, 240, 255))
# visor
d.rounded_rectangle((160 * S, 195 * S, 352 * S, 300 * S), radius=44 * S, fill=(15, 23, 42, 255))
# eyes (glow)
eg = Image.new("RGBA", (W, W), (0, 0, 0, 0))
ed = ImageDraw.Draw(eg)
for ex in (215, 297):
    r = 30 * S
    ed.ellipse((ex * S - r, 247 * S - r, ex * S + r, 247 * S + r), fill=(45, 212, 191, 170))
eg = eg.filter(ImageFilter.GaussianBlur(10 * S))
img.alpha_composite(eg)
d = ImageDraw.Draw(img)
for ex in (215, 297):
    r = 17 * S
    d.ellipse((ex * S - r, 247 * S - r, ex * S + r, 247 * S + r), fill=(94, 234, 212, 255))
    r2 = 6 * S
    d.ellipse(((ex + 5) * S - r2, 240 * S - r2, (ex + 5) * S + r2, 240 * S + r2), fill=(255, 255, 255, 230))
# smile
d.arc((222 * S, 318 * S, 290 * S, 352 * S), start=20, end=160, fill=(100, 116, 139, 255), width=8 * S)

# small rising chart badge (a nod to the stock tools), bottom-right
bx, by, br = 392, 398, 62
d.ellipse(
    ((bx - br) * S, (by - br) * S, (bx + br) * S, (by + br) * S),
    fill=(15, 23, 42, 255),
    outline=(248, 250, 252, 255),
    width=8 * S,
)
pts = [(358, 420), (380, 402), (398, 412), (424, 380)]
d.line([(x * S, y * S) for x, y in pts], fill=(74, 222, 128, 255), width=10 * S, joint="curve")
d.polygon([(424 * S, 372 * S), (432 * S, 392 * S), (410 * S, 386 * S)], fill=(74, 222, 128, 255))

out = img.resize((512, 512), Image.LANCZOS)
out.save(Path(__file__).with_name("assistant-avatar.png"), optimize=True)
print("saved", out.size)
