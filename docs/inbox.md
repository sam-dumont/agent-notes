# The inbox and its review

Restricted MCP sessions (remote clients: claude.ai, the phone, cloud sessions,
ChatGPT) write only under `inbox/`. A handoff, a log entry or a design note is
loaded into later sessions, often a trusted local one with a shell; if a
restricted session could write one, a prompt injection it read (an issue, a web
page) could outlive it. So restricted sessions leave them in typed folders:

| Left in | Promoted to |
|---|---|
| `inbox/handoffs/<project>.md` | `handoffs/<project>.md`, replaced, with a "promoted from the inbox" line |
| `inbox/log/<project>.md` | the top of `log/<project>.md` (newest first) |
| `inbox/decisions/<project>.md` | `design/<project>.md`, under "## Proposed from the inbox" (never your decision table) |
| `inbox/captures/...`, `inbox/*.md` | stays, unless the model names an existing ordinary folder |

## Two reviewers, the same rules

**A trusted session, when you ask.** Claude Code on your machine, or a
claude.ai or ChatGPT session you elevated, reviews the inbox with its own
model through four MCP tools: `list_inbox` (the items, their text marked as
untrusted data, what the floor flagged), then `promote_inbox`, `hold_inbox` or
`delete_note` (duplicates, junk) per item, so an inbox can end empty. No API
key, no job to start: "review my inbox" in the session you already have open.

Two rules hold in code, whatever the session decides:

- **Restricted sessions can't review.** `list_inbox`, `promote_inbox`,
  `hold_inbox` and `delete_note` refuse them.
- **The writer can't review its own item, even elevated.** The server records
  the session (a hash of its connector login) behind every write and append
  to an inbox item, in its own state directory, outside the notes.
  `promote_inbox`, `hold_inbox` and `delete_note` refuse any of those
  sessions, and so a session that wrote an item, got elevated and tried to
  promote it is refused (`lab/compose/oauth_flow.py` replays exactly that). A
  second session, or the scheduled review, judges it. `list_inbox` marks such
  items `"yours": true`. A session that appended to someone else's item is a
  writer too.

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
20,000 bytes; a longer item is held by the floor and never promoted) and a
`sha`. `promote_inbox` and `hold_inbox` take that sha back and refuse an item
that changed since: a restricted session can append to its item at any time,
and the promotion must carry only the text that was judged. The answer is to
call `list_inbox` again. The writer appending after the read doesn't help
it either: the item now needs a fresh read by a session that didn't write it.

**The scheduled review, for the rest.** `reviewer/inbox_review.py`, every
hour, in a CI job of a separate private repo next to your notes repo. Not in
the notes repo itself: git-sync refuses a remote carrying hidden paths such as
`.github/` (`status=refused reason=unsafe_remote`), and that protection stays.
The job checks the notes repo out into `notes/` as data, runs the reviewer
from its own copy, and pushes the result back. Optional: without it, items
wait for a trusted session.

To set it up, make a private repo under the same account as the notes repo,
then:

1. Copy `reviewer/inbox-review-workflow.yaml` to
   `.github/workflows/inbox-review.yaml`, and `reviewer/inbox_review.py` and
   `reviewer/test_inbox_review.py` to `reviewer/` (same paths as here).
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
  What an item *says* (talking to a model, claiming the owner's authority,
  asking to change rules, a command or URL for the next session) is the
  model's call: patterns for that always have another spelling.
  `reviewer/test_inbox_review.py` runs offline before the key is used.
- **What restricted sessions can't do, in code**: overwrite or move anything
  (the inbox is create-only, appends allowed), write `inbox/_held.md` or the
  inbox README, or name a file like an instruction file anywhere.
- **Held items** are listed in `inbox/_held.md` with why. Edit, move or
  delete them yourself (or ask a trusted session to); an unchanged held item isn't judged again (no model call), an
  edited one is. An empty or unchanged inbox costs nothing.
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
