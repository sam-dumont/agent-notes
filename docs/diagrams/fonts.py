"""Swap Excalidraw's fonts for the blog's (render.sh).

Excalidraw can only draw with its own fonts, so the scene uses Excalifont for
titles and labels and Nunito for body text, and this rewrites the SVG to
Inconsolata (bold) and Barlow, the fonts of the blog the figures were drawn
for. An SVG shown through <img> can't load web fonts, so the font files
(Google Fonts, latin subset, SIL OFL; fonts/) are embedded.
"""
import base64
import pathlib
import re
import sys

here = pathlib.Path(__file__).parent / "fonts"


def face(family, weight, file):
    data = base64.b64encode((here / file).read_bytes()).decode()
    return (f"@font-face {{ font-family: {family}; font-weight: {weight}; "
            f"src: url(data:font/woff2;base64,{data}) format('woff2'); }}")


FACES = "\n".join([
    face("Inconsolata", "200 900", "inconsolata-latin.woff2"),
    face("Barlow", 400, "barlow-400-latin.woff2"),
    face("Barlow", 500, "barlow-500-latin.woff2"),
])

path = pathlib.Path(sys.argv[1])
svg = path.read_text(encoding="utf-8")
svg, n = re.subn(r'<style class="style-fonts">.*?</style>',
                 f'<style class="style-fonts">\n{FACES}\n</style>', svg, count=1, flags=re.S)
if not n:
    sys.exit(f"{path}: no style-fonts block")
svg = svg.replace('font-family="Excalifont, Xiaolai, Segoe UI Emoji"',
                  'font-family="Inconsolata, monospace" font-weight="700"')
svg = svg.replace('font-family="Nunito, Segoe UI Emoji"', 'font-family="Barlow, sans-serif" font-weight="500"')
if "Excalifont" in svg or "Nunito" in svg:
    sys.exit(f"{path}: an Excalidraw font is left")
path.write_text(svg, encoding="utf-8")
