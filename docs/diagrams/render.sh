#!/usr/bin/env bash
# Render the figures (figures.py) to SVG, light and dark, with Excalidraw's own
# exporter in a pinned container (Docker only). Same scene in, same SVG out.
#
#   docs/diagrams/render.sh          # writes docs/diagrams/svg/<figure>-{light,dark}.svg
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/svg"
# renovate: datasource=docker depName=yuzutech/kroki-excalidraw
image="yuzutech/kroki-excalidraw:0.33.0@sha256:f6ed8589e7f5b4a4e38dc5a5c67fd2323e083b8b4f711c342366197f03dc1609"
name="agent-notes-figures-$$"
port=18005
work=$(mktemp -d)
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT

docker run -d --rm --name "$name" --platform linux/amd64 -p "127.0.0.1:$port:8004" "$image" >/dev/null
for _ in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$port/" && break; sleep 1; done

mkdir -p "$out"
for fig in overview gate tiers inbox session; do
  for theme in light dark; do
    python3 "$here/figures.py" "$fig" "$theme" "$work/$fig-$theme.json"
    curl -sf --data-binary "@$work/$fig-$theme.json" -H 'Content-Type: text/plain' \
      "http://127.0.0.1:$port/" -o "$out/$fig-$theme.svg"
    python3 "$here/pad.py" "$out/$fig-$theme.svg"
    python3 "$here/fonts.py" "$out/$fig-$theme.svg"
  done
done
echo "wrote $(ls "$out" | wc -l | tr -d ' ') files to docs/diagrams/svg"
