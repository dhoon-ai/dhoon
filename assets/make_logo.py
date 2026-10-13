"""Makes the Dhoon icon set from assets/dhoon-logo.svg (original artwork, MIT).

  config/branding/   the icons LibreChat shows (browser tab, login page, phone home screen)
  assets/social-preview.png   the picture GitHub shows when the repo link is shared

Run:  python3 assets/make_logo.py   (needs CairoSVG: pip install cairosvg)
"""

import re
from pathlib import Path

import cairosvg

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
OUT = KIT / "config" / "branding"
SVG = (HERE / "dhoon-logo.svg").read_text()
BG_RECT = '<rect width="512" height="512" rx="112" fill="url(#bg)"/>'
assert BG_RECT in SVG, "dhoon-logo.svg changed: update BG_RECT"


def png(svg: str, name: str, size: int, folder: Path = OUT) -> None:
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(folder / name), output_width=size, output_height=size)
    print("saved", (folder / name).relative_to(KIT), f"{size}x{size}")


def maskable() -> str:
    """Phones cut icons into circles or squircles: fill the whole square, artwork in the middle 70%."""
    head, body = SVG.split(BG_RECT)
    body = body.replace("</svg>", "")
    return (
        f'{head}<rect width="512" height="512" fill="url(#bg)"/>'
        f'<g transform="translate(256 256) scale(0.7) translate(-256 -256)">{body}</g></svg>'
    )


def social() -> str:
    """1280x640 card: logo, name and tagline."""
    inner = re.search(r"<svg[^>]*>(.*)</svg>", SVG, re.S).group(1)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640">
  <defs><linearGradient id="card" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#1e0b45"/><stop offset="1" stop-color="#0b0716"/></linearGradient></defs>
  <rect width="1280" height="640" fill="url(#card)"/>
  <svg x="110" y="150" width="340" height="340" viewBox="0 0 512 512">{inner}</svg>
  <g font-family="Inter, DejaVu Sans, sans-serif" fill="#fff7ed">
    <text x="510" y="300" font-size="112" font-weight="700">Dhoon</text>
    <text x="514" y="372" font-size="38" fill="#fde68a">Your private AI, with the drive</text>
    <text x="514" y="422" font-size="38" fill="#fde68a">to get things done.</text>
    <text x="514" y="500" font-size="26" fill="#fb923c">Runs on your own computer · open source</text>
  </g>
</svg>"""


OUT.mkdir(parents=True, exist_ok=True)
(OUT / "logo.svg").write_text(SVG)
print("saved config/branding/logo.svg")
png(SVG, "favicon-16x16.png", 16)
png(SVG, "favicon-32x32.png", 32)
png(SVG, "icon-192x192.png", 192)
png(maskable(), "apple-touch-icon-180x180.png", 180)  # iOS rounds the corners itself
png(maskable(), "maskable-icon.png", 512)
cairosvg.svg2png(bytestring=social().encode(), write_to=str(HERE / "social-preview.png"))
print("saved assets/social-preview.png 1280x640")
