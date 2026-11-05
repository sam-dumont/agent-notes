# Inbox

Everything a restricted session (claude.ai, the phone, cloud sessions,
ChatGPT) writes lands here: it can't write anywhere else. A trusted session
reviews it when the owner asks (`list_inbox`, then `promote_inbox`, `hold_inbox` or `delete_note`),
the hourly review otherwise; what is held is listed in `inbox/_held.md`.

| Folder | For | Promoted to |
|---|---|---|
| `inbox/handoffs/<project>.md` | the session's handoff | `handoffs/<project>.md` |
| `inbox/log/<project>.md` | a log entry | the top of `log/<project>.md` |
| `inbox/decisions/<project>.md` | decisions to record | `design/<project>.md`, under "Proposed from the inbox" |
| `inbox/captures/` | anything else | stays for the owner, or an existing folder |

`<project>` is the project's file name in `handoffs/`, `log/` or `design/`.
