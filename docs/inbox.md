# The inbox and its review

Restricted MCP sessions (remote clients: claude.ai, the phone, cloud sessions,
ChatGPT) write only under `inbox/`. A handoff, a log entry or a design note is
loaded into later sessions, often a trusted local one with a shell; if a
restricted session could write one, a prompt injection it read (an issue, a web
page) could outlive it. So restricted sessions leave them in typed folders:

| Left in | Promoted to |
|---|---|
| `inbox/handoffs/<project>.md` | `handoffs/<project>.md`, replaced, with a "promoted from the inbox" line |
| `inbox/log/<project>.md` | `log/<project>.md`, above the newest entry, keeping the entry's own dated heading |
| `inbox/decisions/<project>.md` | `design/<project>.md`, under "## Proposed from the inbox" (never your decision table) |
| `inbox/captures/...`, `inbox/*.md` | stays, unless the model names an existing ordinary folder |

## An item is one dated entry

Sessions append to the same inbox file over days: a status at each milestone,
a log entry per piece of work. So the review doesn't judge a file, it judges
each entry in it. An entry starts at a dated H2 outside a code fence
(`## 2026-11-08 ...`); undated H2s (`## Next step`) belong to the entry they
are in, and the text before the first dated heading is an entry too, unless
it is only a title. The server tells every client to start each append with
such a heading.

Each entry has its own `sha`, is judged, held or promoted on its own, and
leaves its file when promoted; the file goes with its last entry. One held
entry doesn't keep the others back, and the 20,000-byte floor is per entry,
so a file that keeps growing never gets held for good. An append that runs on
into an entry (no dated heading of its own) changes that entry's `sha`, so it
is judged again.

**Sessions save their status as they go.** The server's instructions tell
every client to append a short status to `inbox/handoffs/<project>.md` each
time something is decided or finished or the next step changes, not only at
the end, so a session on another device can pick it up once it's promoted.

## Two reviewers, the same rules

**A trusted session, when you ask.** Claude Code on your machine, or a
claude.ai or ChatGPT session you elevated, reviews the inbox with its own
model through four MCP tools: `list_inbox` (the entries, their text marked as
untrusted data, what the floor flagged, `entry: "2 of 3"` for their place in
the file), then `promote_inbox` or `hold_inbox` per entry, or `delete_note` for
a whole file whose entries are all duplicates or junk, so an inbox can end
empty. No API key, no job to start: "review my inbox" in the session you
already have open.

Two rules hold in code, whatever the session decides:

- **Restricted sessions can't review.** `list_inbox`, `promote_inbox`,
  `hold_inbox` and `delete_note` refuse them.
- **The writer can't review its own item, even elevated.** The server records
  the session (a hash of its connector login) behind every restricted write
  and append to an inbox file, in its own state directory, outside the notes.
  `promote_inbox`, `hold_inbox`, `delete_note` and `move_note` refuse any of
  those sessions, and so a session that wrote an item while restricted, got
  elevated and tried to promote it is refused (`lab/compose/oauth_flow.py`
  replays exactly that). A second session, or the scheduled review, judges it.
  `list_inbox` marks such items `"yours": true`. A restricted session that
  appended to someone else's file is a writer too. A trusted session's own
  inbox writes are not recorded: it could write `handoffs/` or `design/`
  directly, so reviewing its own inbox text gains it nothing, and sessions on
  one machine often share one login, which would lock each out of what the
  others wrote.

**To review now, without phrasing it**: the server publishes an MCP prompt,
`review_inbox` (Claude Code shows it as `/mcp__<server>__review_inbox`, with
an optional project name to review only that project's handoff, log and
decisions; claude.ai lists it in its prompt menu). Its text is the procedure
above wrapped around the scheduled reviewer's own rule text (`RULES` in
`reviewer/inbox_review.py`, also in `list_inbox`'s note), so the two judge
alike. From a restricted session it asks for elevation first; the tools
refuse it anyway. Or start the scheduled job early:
`gh workflow run inbox-review.yaml` in the automation repo (below).

Each item in `list_inbox` comes with its whole text (up to the reviewer's
20,000 bytes; a longer entry is held by the floor and never promoted) and a
`sha`. `promote_inbox` and `hold_inbox` take that sha back and refuse an entry
that changed since: a restricted session can append to its file at any time,
and the promotion must carry only the text that was judged. The answer is to
call `list_inbox` again. The writer appending after the read doesn't help
it either: the entry now needs a fresh read by a session that didn't write it.

**The scheduled review, for the rest.** `reviewer/inbox_review.py`, every
hour, in a CI job of a separate private repo next to your notes repo. Not in
the notes repo itself: git-sync refuses a remote carrying hidden paths such as
`.github/` (`status=refused reason=unsafe_remote`), and that protection stays.
The job checks the notes repo out into `notes/` as data, runs the reviewer
from its own copy, and pushes the result back. Optional: without it, items
wait for a trusted session.

The job sends the model one entry per call (an injected entry can't sway the
verdict on another), judges at most 40 entries per run (the rest wait for the
next run, as does an entry whose call failed), and builds one API client for
the run. GitHub starts scheduled runs late, sometimes by hours, and can skip
them, so a review usually comes within a few hours; when a status must reach
the handoff now, ask a trusted session to review.

To set it up, make a private repo under the same account as the notes repo,
then:

1. Copy `reviewer/inbox-review-workflow.yaml` to
   `.github/workflows/inbox-review.yaml`, and `reviewer/inbox_review.py`,
   `reviewer/test_inbox_review.py`, `reviewer/vault_tidy.py` and
   `reviewer/test_vault_tidy.py` to `reviewer/` (same paths as here).
2. Add a repository variable `NOTES_REPO_NAME`: the notes repo's name.
3. Add the secrets:
   - `ANTHROPIC_API_KEY`: a key for this job only, with a spend limit.
   - A way to write to the notes repo, and only to it. Either
     `NOTES_REPO_TOKEN`, a fine-grained token limited to the notes repo with
     Contents read and write; or a GitHub App with Contents read and write
     installed on the notes repo only, as a variable `NOTES_APP_CLIENT_ID` and
     a secret `NOTES_APP_PRIVATE_KEY` (the job then mints a 1-hour token
     narrowed to that repo). Don't reuse git-sync's App key: one key per
     place it lives.
4. Run it once dry: `gh workflow run inbox-review.yaml -f dry_run=true`, and
   read the job summary.

**The tidy, after each review.** The same job then runs
`reviewer/vault_tidy.py`: no model, no key, nothing dropped, and a second run
changes nothing. A log page (`log/<project>.md`) keeps the entries of the last
30 days within 40 KB, newest first; older entries move to
`log/archive/<project>/YYYY-MM.md`, one page per month, linked from the log's
intro, so the page a session reads first stays short. It also folds an empty
"(from the inbox ...)" heading left above an entry's own dated heading into
that entry.

**Restricted sessions have a quota.** Every restricted write lands in the
inbox and every entry costs the scheduled review a model call, so a session
looping on `write_note` (or told to by text it read) could fill the disk and
the budget. A client may create at most 100 inbox items in 24 hours (appending
to its own is free), and the whole inbox may hold at most 2 MB; past either,
the write is refused with why. The creation counts live in memory; the size
cap reads the disk. Trusted sessions have no quota.

**You hear about what's waiting.** While items wait, the MCP server adds one
line to a tool result ("2 inbox items waiting for review"), again when the
count changes or after 15 minutes, and the session hook puts the count in a
Claude Code session's context at start.

Both reviewers share the code below: the session's model replaces the
scheduled job's model as the judge, nothing else changes.

- **The model only judges** (promote or hold, and why), with the items as
  untrusted data. The code does the moves, only along the table above, never
  into `agents/`, `AGENTS.md`, `CLAUDE.md`, `index.md` or a hidden path.
- **A floor in code, on structure only**, holds whatever the model says:
  invisible or control characters (after NFKC normalisation: bidi controls,
  zero-width and Unicode tag characters, which models read and people don't)
  and file names agents load as instructions (`CLAUDE.md`, `AGENTS.md`...).
  What an item *says* (talking to a model, using the owner's authority to
  make a session do something, asking to change rules, a command or URL for
  the next session) is the model's call: patterns for that always have
  another spelling. A decision that quotes the owner's words as its why is
  not held for that: it lands quoted under "Proposed from the inbox", and only
  you make it Active.
  `reviewer/test_inbox_review.py` runs offline before the key is used.
- **What restricted sessions can't do, in code**: overwrite or move anything
  (the inbox is create-only, appends allowed), write `inbox/_held.md` or the
  inbox README, or name a file like an instruction file anywhere.
- **Held entries** are listed in `inbox/_held.md` with why and their first
  line, keyed by file and entry `sha` (a `_held.md` keyed by file alone still
  reads). Edit, move or delete them yourself (or ask a trusted session to); an
  unchanged held entry isn't judged again (no model call), an edited one is.
  An empty or unchanged inbox costs nothing.
- **A sandbox by construction**: the job has a fresh checkout of the notes as
  data, a token that writes the notes repo and nothing else, and the model
  key; no cluster, no other credential. The code it runs comes from the
  automation repo, never from the notes.
  `promote_inbox` refuses an item the floor flags, whatever the session says:
  those only move by your hand.

## Handoffs are data

Even promoted, a handoff was written by an earlier session. The session hook
(`hooks/`) injects it fenced and labelled as quoted data, with a reminder that
it is context, not instructions, and never a reason to run a command.

One handoff per project, so parallel worktrees overwrite each other's. The hook
says when the handoff was written on another branch.

## Search skips the inbox

`search_notes` leaves `inbox/` out unless you search inside it, and then each
hit says `"unreviewed": true`.
