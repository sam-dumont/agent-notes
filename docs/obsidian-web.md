# Obsidian in a browser

Optional (profile `web`). [Ignis](https://github.com/Nystik-gh/ignis) serves
Obsidian's own desktop app as a web page: the editor, the graph, canvas,
plugins, themes. It is not a remote desktop.

- **The app bundle is Obsidian's.** It is downloaded from Obsidian's own
  releases at deploy time, checked against a SHA-256, and never stored in this
  repository or an image. Check that your use fits Obsidian's license.
- **Ignis has no login.** oauth2-proxy stands in front of it, and only
  `WEB_ALLOWED_EMAIL` gets through: no email domains are configured, so every
  other account at your provider gets a 403 after logging in (the end-to-end
  test checks it). The gate refuses the calls that create, rename or remove
  whole vaults.
- **It runs with your full rights.** No MCP tiers here: whoever is let in edits
  every note, rules included.
- **It sees the notes folder only**, not the LiveSync database, the git dir or
  the MCP server's state.
- Its edits are file writes, so they reach the devices and git like any other.

Ignis is AGPL-licensed; this repository only runs its published image.
