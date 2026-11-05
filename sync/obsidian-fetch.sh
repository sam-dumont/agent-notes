#!/bin/sh
# Puts Obsidian's app bundle in /app/obsidian-app for Ignis (Obsidian in a
# browser), as a one-shot step before it starts. Replaces the Ignis image's
# own entrypoint, which needs root (useradd, chown, gosu, a global npm
# install). Pinned twice: the bundle by SHA-256, the asar extractor by its
# lockfile. The bundle is Obsidian's own app, downloaded from Obsidian's
# releases at deploy time and never redistributed (docs/obsidian-web.md).
set -eu

: "${OBSIDIAN_VERSION:?}" "${OBSIDIAN_SHA256:?}"
dest=/app/obsidian-app
work=/tmp/obsidian

rm -rf "$work" && mkdir -p "$work"
curl -fsSL --retry 3 -o "$work/obsidian.asar.gz" \
  "https://github.com/obsidianmd/obsidian-releases/releases/download/v${OBSIDIAN_VERSION}/obsidian-${OBSIDIAN_VERSION}.asar.gz"
echo "${OBSIDIAN_SHA256}  $work/obsidian.asar.gz" | sha256sum -c -
gunzip "$work/obsidian.asar.gz"
# The extractor from a committed lockfile (sync/asar), no install scripts:
# nothing resolved fresh from npm at deploy time.
cp "${ASAR_PACKAGE:-/scripts/asar/package.json}" "$work/package.json"
cp "${ASAR_LOCK:-/scripts/asar/package-lock.json}" "$work/package-lock.json"
(cd "$work" && npm ci --ignore-scripts --no-audit --no-fund --silent)
# Through node: /tmp is a no-exec tmpfs in both deployments.
node "$work/node_modules/@electron/asar/bin/asar.mjs" extract "$work/obsidian.asar" "$dest"
test -f "$dest/index.html"
rm -rf "$work"
echo "Obsidian ${OBSIDIAN_VERSION} ready"
