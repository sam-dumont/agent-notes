"""The figures for the README, the docs and the blog post. Usage: figures.py <figure> <theme> <out.json>

Hand-drawn style (Excalidraw), rendered to SVG by render.sh. Ideas, not addresses: no host names,
no IPs, nothing specific to one deployment.
"""

import sys

from xkit import HAND, NORMAL, Scene

CH = 76  # card height


def stub(s, eid, x, y):
    """An invisible anchor so several arrows can land on one tall box at different heights."""
    s.boxes[eid] = (x, y, 1, CH)


def overview(s):
    s.title("title", 0, 0, "One folder, four ways in",
            "Plain Markdown in the middle. Every way in reads and writes the same files, and an edit anywhere shows up everywhere within a minute.")
    top, step = 170, 112
    rows = [top + i * step for i in range(4)]
    s.zone("who", 0, top - 70, 330, 4 * step + 40, "Who", "neutral", dashed=True)
    s.zone("doors", 390, top - 70, 420, 4 * step + 40, "The doors", "paris")
    s.card("obsidian", 20, rows[0], 290, CH, "Obsidian", "your phone, your laptop", "phone", "out")
    s.card("repo", 20, rows[1], 290, CH, "Agents in a repo", "cloud sessions, CI", "git", "out")
    s.card("mcp_users", 20, rows[2], 290, CH, "AI agents", "Claude, ChatGPT, Claude Code", "decision", "out")
    s.card("browser", 20, rows[3], 290, CH, "Any browser", "a laptop you can't install on", "browser", "out")
    s.card("sync", 410, rows[0], 380, CH, "LiveSync + CouchDB", "end-to-end encrypted, a key per device", "database", "paris")
    s.card("git", 410, rows[1], 380, CH, "Your private git repo", "every change is a commit", "git", "paris")
    s.card("mcp", 410, rows[2], 380, CH, "The MCP server", "your OIDC login, two tiers of rights", "sso", "paris")
    s.card("web", 410, rows[3], 380, CH, "Obsidian in a browser", "the real app, behind SSO", "browser", "paris")
    # One folder, not four: every door reads and writes all of it, so the arrows
    # converge on the folder and its subfolders are a list, not targets.
    fx, fw = 1010, 320
    zone_top, zone_h = top - 70, 4 * step + 40
    s.zone("folder", fx, zone_top, fw, zone_h, "The notes folder", "store")
    for i, (n, sub) in enumerate([("agents/", "the rules every session reads"), ("projects/, design/", "pages and decisions"),
                                   ("handoffs/, log/", "where sessions stopped"), ("inbox/", "what remote sessions leave")]):
        y = top + 10 + i * 100
        s.text(f"md{i}", fx + 28, y, fw - 50, n, size=20, align="left", font=s.font)
        s.text(f"md{i}:s", fx + 28, y + 28, fw - 50, sub, size=15, align="left", color=s.p["muted"])
    stub(s, "folder_in", fx, zone_top + zone_h / 2 - CH / 2)
    for i, (who, door) in enumerate([("obsidian", "sync"), ("repo", "git"), ("mcp_users", "mcp"), ("browser", "web")]):
        s.arrow(f"a{i}", who, door)
        label = ["daemon", "git-sync", "tools", "files"][i]
        # The label sits on its own arrow, a third of the way along.
        sx, sy = 410 + 380 + 6, rows[i] + CH / 2
        ex, ey = fx - 6, zone_top + zone_h / 2
        t = 0.33
        s.arrow(f"b{i}", door, "folder_in", both=True, kind="opt", label=label,
                label_at=(sx + t * (ex - sx), sy + t * (ey - sy) - 26))

def gate(s):
    s.title("title", 0, 0, "The gate in front of CouchDB",
            "LiveSync signs every device token as a CouchDB admin. CouchDB can't narrow that down, so a small proxy does.")
    y = 190
    s.card("device", 0, y, 300, CH, "A device", "a token signed with its own key", "phone", "out")
    s.card("gate", 440, y, 320, CH, "The gate", "Caddy, the only way in", "firewall", "paris")
    s.card("couch", 900, y, 320, CH, "CouchDB", "holds ciphertext only", "database", "paris")
    s.arrow("a1", "device", "gate", label="Bearer token", label_at=(370, y - 34))
    s.arrow("a2", "gate", "couch", label="documents only", label_at=(830, y - 34))
    s.card("no", 440, y + 150, 780, 110, "Refused, whatever the token says",
           "other databases, _users, _config, _security, design documents,\n"
           "dropping or recreating the database, encoded slashes, basic auth", "block", "out")
    s.arrow("a3", "gate", "no", "bt", kind="opt")
    s.note("n", 0, y + 150, 300, 110, "Revoke a device:\ndelete its public key")


def tiers(s):
    s.title("title", 0, 0, "Who gets what",
            "Every caller is you. What differs is the way in: a session that just read a booby-trapped issue looks exactly like a careful one.")
    y = 180
    s.zone("r", 0, y - 70, 560, 290, "Restricted (default)", "out")
    s.card("r1", 20, y, 520, CH, "claude.ai, the phone, cloud sessions", "ChatGPT, any other machine", "decision", "out")
    s.card("r2", 20, y + 100, 520, CH, "Reads everything", "writes only the inbox, deletes nothing", "eye", "out")
    s.zone("t", 620, y - 70, 560, 290, "Trusted", "home")
    s.card("t1", 640, y, 520, CH, "Your enrolled machine", "a device key in its Keychain", "home", "home")
    s.card("t2", 640, y + 100, 520, CH, "A session you approved", "four hours, that session only", "sso", "home")
    y2 = y + 300
    s.text("flow", 0, y2, 1180, "How a restricted session gets more", size=24, align="left", font=s.font)
    y3 = y2 + 50
    w = 270
    xs = [0, 300, 600, 900]
    s.card("e1", xs[0], y3, w, CH, "It asks", "with a reason", "decision", "out")
    s.card("e2", xs[1], y3, w, CH, "You get a link", "and a 4-letter code", "phone", "paris")
    s.card("e3", xs[2], y3, w, CH, "Fresh login", "passkey or a second factor", "sso", "paris")
    s.card("e4", xs[3], y3, w, CH, "Approve", "the session gets a grant", "home", "home")
    for i in range(3):
        s.arrow(f"f{i}", f"e{i + 1}", f"e{i + 2}")
    s.note("n", 0, y3 + 110, 1170, 50, "A prompt-injected session can ask. It can't approve: that takes your second factor, on your device.")


def inbox(s):
    s.title("title", 0, 0, "The inbox and its review",
            "What a restricted session writes is quarantined. A trusted session (when you ask) or an hourly job promotes what is safe: the model judges, the code moves.")
    y = 190
    s.card("sess", 0, y + 90, 280, CH, "Restricted session", "claude.ai, the phone...", "decision", "out")
    s.zone("in", 320, y - 60, 330, 420, "inbox/", "out", dashed=True)
    names = [("handoffs/", "where it stopped"), ("log/", "what it did"), ("decisions/", "what it decided"), ("captures/", "anything else")]
    for i, (n, sub) in enumerate(names):
        s.card(f"i{i}", 340, y + i * 88, 290, 70, n, sub, "docs", "out")
    s.arrow("w", "sess", "in", label="writes only here", label_at=(230, y + 40))
    s.card("rev", 720, y + 40, 300, 96, "The reviewer", "a trusted session, or hourly", "actions", "paris")
    s.card("floor", 720, y + 200, 300, 96, "A floor in code", "holds commands, URLs, rules talk", "block", "paris")
    s.arrow("r", "in", "rev")
    s.arrow("rf", "rev", "floor", "bt", kind="opt")
    s.text("rfl", 885, y + 152, 200, "model: promote or hold", size=15, align="left", color=s.p["muted"], font=s.font)
    x = 1090
    s.card("d1", x, y - 40, 300, 70, "handoffs/<project>.md", "with a provenance line", "docs", "home")
    s.card("d2", x, y + 50, 300, 70, "log/<project>.md", "at the top", "docs", "home")
    s.card("d3", x, y + 140, 300, 70, "design/<project>.md", "\"proposed\", never the table", "docs", "home")
    s.card("d4", x, y + 230, 300, 70, "inbox/_held.md", "held, with why", "mute", "neutral")
    for i in range(1, 5):
        s.arrow(f"m{i}", "rev" if i < 4 else "floor", f"d{i}", kind="main" if i < 4 else "opt")
    s.note("never", 1090, y + 330, 300, 56, "never: agents/, AGENTS.md,\nCLAUDE.md, index.md")


def session(s):
    s.title("title", 0, 0, "A session's life",
            "Two Claude Code hooks close the loop: the rules come in at the start, a log and a handoff go out at the end.")
    y = 180
    s.card("start", 0, y, 360, 96, "Session start", "the rules digest, and the last\nhandoff quoted as data", "docs", "paris")
    s.card("work", 420, y, 360, 96, "The session works", "reads and writes notes over MCP,\nin its tier", "decision", "out")
    s.card("end", 840, y, 440, 96, "Session end", "a summary, scrubbed of secrets", "actions", "paris")
    s.arrow("a1", "start", "work")
    s.arrow("a2", "work", "end")
    # log and handoff right under the end, so their arrows are short and straight.
    y2 = y + 170
    s.card("log", 840, y2, 210, CH, "log/", "newest first", "docs", "store")
    s.card("handoff", 1070, y2, 210, CH, "handoffs/", "for the next session", "docs", "store")
    s.boxes["end_l"] = (945, y, 1, 96)
    s.boxes["end_h"] = (1175, y, 1, 96)
    s.arrow("b1", "end_l", "log", "bt")
    s.arrow("b2", "end_h", "handoff", "bt")
    # The loop back: straight segments, square corners, the label under the line.
    yb = y2 + CH + 60
    s.arrow("c1", "handoff", "start", "bl", kind="opt", sharp=True,
            via=[(1175, yb), (-50, yb), (-50, y + 48)])
    s.text("c1:l", 300, yb + 12, 520, "the next session, on any device", size=16, color=s.p["note"], font=s.font)
    s.note("n", 0, y2, 780, CH, "A remote session writes its handoff to the inbox:\nthe reviewer promotes it, or holds it for you.")

FIGURES = {"overview": overview, "gate": gate, "tiers": tiers, "inbox": inbox, "session": session}

if __name__ == "__main__":
    name, theme, out = sys.argv[1], sys.argv[2], sys.argv[3]
    scene = Scene(theme, font=HAND, body_font=NORMAL)
    FIGURES[name](scene)
    scene.dump(out)
