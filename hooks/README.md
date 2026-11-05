# Session hooks

Two Claude Code hooks and a header helper, so every session starts from what the last one
left and ends by writing it down. Python 3.11+, the standard library, `git`, and optionally
`gh` and `claude`.

| File | What it does |
|---|---|
| `agent_notes_hooks.py start` | SessionStart: injects your rules digest (`agents/digest.md` by default) and, for a mapped project, its handoff into the new session |
| `agent_notes_hooks.py end` | SessionEnd: returns at once; a detached worker has `claude -p` summarise the session, adds a dated entry at the top of `log/<project>.md` and overwrites `handoffs/<project>.md` |
| `agent_notes_hooks.py checkpoint` | Stop: the same worker, at most every 30 minutes per session, so a long session reaches the notes while it runs (other devices see its decisions the same hour, and a day-long session isn't cut down to its last hour by the summariser's budget). `--force` on PreCompact: always, before the context is compressed |
| `mcp-device-header` | Claude Code `headersHelper`: signs a 12-hour device token so the MCP server gives this machine the trusted tier |
| `enroll-device` | creates this machine's device key and prints the public key for the server |

A session that goes on after `/clear` (or gets resumed) is logged in segments: each end event
logs only what came after the previous one, so nothing is rewritten and nothing is lost. The
state lives in `~/.local/state/agent-notes/state.json`. Writing the same entry twice is a no-op.

Every hook fails open. A missing config, a dead network or a broken `claude` login leaves a
line in `~/.local/state/agent-notes/hooks.log` and the session carries on.

## Setup

1. Copy `hooks.example.toml` to `~/.config/agent-notes/hooks.toml` and fill it in:
   - where the notes are: a local clone (writes are a commit and a push) or a GitHub repo
     (writes go through `gh api` with your `gh` login);
   - which repos map to which project;
   - where the rules apply (`scope.roots`) and where nothing should happen at all
     (`scope.exclude`: a client's code that runs under their rules, say);
   - extra redaction patterns. The built-in ones already remove keys, tokens, JWTs, email and
     IP addresses and long opaque strings before anything is written.
2. Add the two hooks to `~/.claude/settings.json` (the `hooks` part of
   `settings.example.json`; use the absolute path of your checkout).
3. Try it: `echo '{"cwd": "'$PWD'"}' | python3 hooks/agent_notes_hooks.py start` prints the
   context a session would get. `--dry-run` on `end` and `worker` prints instead of writing.

The summary prompt tells the model to leave out secrets, money and people's names, and to treat
the transcript as data. That's a request to a model, so the redaction runs on its output anyway.

## Device trust (optional)

By default every MCP session from a remote client is restricted and has to ask for elevation.
An enrolled machine skips that: its sessions are trusted.

1. `[device]` in `hooks.toml`: a `kid` for this machine, where the key lives (`keychain` on
   macOS, `file:<path>` elsewhere, mode 600), and under `[device.servers]` the server URL with
   its `AGENT_NOTES_NAME`. Only listed servers ever get a token.
2. `hooks/enroll-device`. The private key goes to the Keychain or the file, never to your
   terminal. It prints a JSON entry: merge it into `AGENT_NOTES_DEVICE_KEYS` on the server and
   redeploy.
3. Add `"headersHelper": "<checkout>/hooks/mcp-device-header"` to the server's entry in
   `~/.claude.json` (the `mcpServers` part of `settings.example.json`; `claude mcp add` writes
   that file, not `settings.json`).

**An enrolled key is as good as a root SSH key.** Whoever can use it gets the trusted tier
on that server without an approval. The OIDC login still applies, but on your own machine
that login is usually already there. Enroll machines you'd trust with your SSH keys, nothing
else.

To revoke: remove the `kid` from `AGENT_NOTES_DEVICE_KEYS` and redeploy (the server refuses
its tokens from the restart on), then delete the key (`security delete-generic-password -s
agent-notes-device-key -a <kid>`, or the file).

## Tests

```bash
cd hooks && uv run --with pytest pytest -q            # the server check skips
cd server && uv run pytest -q ../hooks/test_hooks.py  # all, against server/access.py
```
