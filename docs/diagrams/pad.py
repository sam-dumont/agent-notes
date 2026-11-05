"""Give a rendered figure a real margin (render.sh).

The Excalidraw exporter in kroki ignores the scene's exportPadding and crops
about 10 px from the outermost stroke, so frames touch the image edge. This
widens the viewBox by PAD on every side and grows the background to match.
"""
import re
import sys

PAD = 48


def main(path):
    svg = open(path, encoding="utf-8").read()
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)
    if not m:
        sys.exit(f"{path}: no viewBox from 0 0, already padded?")
    w, h = float(m.group(1)), float(m.group(2))
    W, H = w + 2 * PAD, h + 2 * PAD
    svg = svg.replace(m.group(0), f'viewBox="{-PAD} {-PAD} {W:g} {H:g}" width="{W:g}" height="{H:g}"', 1)
    bg = re.search(r'<rect x="0" y="0" width="[\d.]+" height="[\d.]+"', svg)
    if bg:
        svg = svg.replace(bg.group(0), f'<rect x="{-PAD}" y="{-PAD}" width="{W:g}" height="{H:g}"', 1)
    open(path, "w", encoding="utf-8").write(svg)


if __name__ == "__main__":
    main(sys.argv[1])
