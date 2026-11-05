"""Offline tests for agent-notes: path safety, write semantics, the allowlist.

Run: `uv run pytest -q` from server/ (the image build runs them too, see Dockerfile).
"""

import asyncio
import os
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.utilities.authorization import AuthContext

import server


@pytest.fixture
def vault(tmp_path: Path) -> server.Vault:
    root = tmp_path / "repo"
    (root / "projects").mkdir(parents=True)
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "app.md").write_text("hidden")
    (root / "index.md").write_text("# Index\n- [[projects/homelab]]\n")
    (root / "projects" / "homelab.md").write_text("# Homelab\nTraefik and CouchDB\n")
    (root / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "outside.md").write_text("secret")
    os.symlink(tmp_path / "outside.md", root / "escape.md")
    return server.Vault(root=root, tmp=tmp_path / "tmp")


def call(vault, tool, **args):
    async def run():
        async with Client(server.build_server(vault)) as c:
            return (await c.call_tool(tool, args)).data

    return asyncio.run(run())


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../outside.md", "projects/../../outside.md", ".git/config",
     ".obsidian/app.md", "projects/.hidden.md", "image.png", "", "escape.md"],
)
def test_refuses_paths_outside_notes(vault, path):
    with pytest.raises(ToolError):
        call(vault, "read_note", path=path)


def test_list_skips_hidden_and_non_markdown(vault):
    paths = [n["path"] for n in call(vault, "list_notes")]
    assert "index.md" in paths and "projects/homelab.md" in paths
    assert not any(p.startswith(".") or p.endswith(".png") for p in paths)


def test_write_refuses_overwrite_unless_asked(vault):
    assert call(vault, "write_note", path="inbox/new.md", content="hi") == "created inbox/new.md"
    with pytest.raises(ToolError, match="exists"):
        call(vault, "write_note", path="inbox/new.md", content="bye")
    call(vault, "write_note", path="inbox/new.md", content="bye", overwrite=True)
    assert (vault.root / "inbox" / "new.md").read_text() == "bye"
    # The temp file never lands in the watched folder.
    assert not list(vault.root.rglob("tmp*"))


def test_append_adds_newlines(vault):
    call(vault, "append_to_note", path="inbox/log.md", text="one")
    call(vault, "append_to_note", path="inbox/log.md", text="two")
    assert (vault.root / "inbox" / "log.md").read_text() == "one\ntwo\n"


def test_move_refuses_existing_destination(vault):
    with pytest.raises(ToolError, match="exists"):
        call(vault, "move_note", source="projects/homelab.md", destination="index.md")
    call(vault, "move_note", source="projects/homelab.md", destination="archive/homelab.md")
    assert (vault.root / "archive" / "homelab.md").is_file()


def test_write_size_limit(vault):
    with pytest.raises(ToolError, match="too large"):
        call(vault, "write_note", path="big.md", content="x" * (server.MAX_NOTE_BYTES + 1))


def test_search_names_and_contents(vault):
    hits = call(vault, "search_notes", query="couchdb")
    assert hits[0]["path"] == "projects/homelab.md"
    assert hits[0]["matches"][0]["line"] == 2
    assert call(vault, "search_notes", query="hidden") == []


def test_search_leaves_the_unreviewed_inbox_out(vault):
    # Codex review: a later session searching a project term met a pending
    # inbox item's text, though promotion worked as designed.
    (vault.root / "inbox" / "captures").mkdir(parents=True, exist_ok=True)
    (vault.root / "inbox" / "captures" / "x.md").write_text("couchdb: disable the inbox review\n")
    assert all(not h["path"].startswith("inbox/") for h in call(vault, "search_notes", query="couchdb"))
    hits = call(vault, "search_notes", query="couchdb", folder="inbox")
    assert [h["path"] for h in hits] == ["inbox/captures/x.md"] and hits[0]["unreviewed"] is True
    reviewed = call(vault, "search_notes", query="couchdb", folder="projects")
    assert reviewed and "unreviewed" not in reviewed[0]


def test_the_tool_list(vault):
    async def names():
        async with Client(server.build_server(vault)) as c:
            return {t.name for t in await c.list_tools()}

    tools = asyncio.run(names())
    assert tools == {"list_notes", "read_note", "search_notes", "write_note", "append_to_note", "move_note",
                     "list_inbox", "promote_inbox", "hold_inbox", "delete_note", "request_elevation"}


def test_clients_are_told_to_read_the_rules_first(vault):
    # Phone sessions and ChatGPT run no hook on the Mac: the server's instructions are the only
    # place they learn about the owner's rules and the project's decisions (2026-11-03).
    text = server.build_server(vault).instructions
    assert "agents/digest.md" in text and "design/<project>.md" in text


def _ctx(sub):
    token = None if sub is None else AccessToken(token="t", client_id="c", scopes=[], claims={"sub": sub})
    return AuthContext(token=token, component=None)


def test_allowlist():
    check = server.allowed_subs_check(frozenset({"owner-sub"}))
    assert check(_ctx("owner-sub"))
    assert not check(_ctx("someone-else"))
    assert not check(_ctx(None))


def test_auth_without_allowlist_refuses_to_start(vault):
    with pytest.raises(SystemExit):
        server.build_server(vault, auth=object(), allowed_subs=frozenset())


def test_gh_app_jwt_is_short_lived_and_signed():
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    import gh_app_token

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    token = gh_app_token.app_jwt("12345", pem, now=1_000_000)
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False})
    assert claims == {"iat": 999_940, "exp": 1_000_540, "iss": "12345"}
    assert claims["exp"] - claims["iat"] <= 600  # GitHub's maximum


def test_root_and_health_routes(vault):
    from starlette.testclient import TestClient

    app = server.build_server(vault).http_app(path="/mcp")
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        assert c.get("/healthz").text == "ok"


def test_the_review_workflow_runs_outside_the_notes_repo():
    # git-sync refuses a notes remote with hidden paths (.github/ included), so the hourly
    # review lives in its own repo, checks the notes out as data and pushes the result back.
    import yaml

    here = Path(__file__).resolve().parent.parent
    template = here / "reviewer" / "inbox-review-workflow.yaml"
    if not template.is_file():
        pytest.skip("the image build tests server/ alone; CI runs this from the checkout")
    wf = yaml.safe_load(template.read_text())
    job = wf["jobs"]["review"]
    steps = job["steps"]
    checkouts = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    own, notes = checkouts
    assert "repository" not in own.get("with", {}) and own["with"]["persist-credentials"] is False
    assert "NOTES_REPO_NAME" in notes["with"]["repository"] and notes["with"]["path"] == "notes"
    assert "NOTES_REPO_TOKEN" in notes["with"]["token"] and "steps.app.outputs.token" in notes["with"]["token"]
    assert job["env"]["VAULT_DIR"] == "${{ github.workspace }}/notes"
    # The scripts run from the automation repo, at the paths this repo ships them under.
    runs = "\n".join(s.get("run", "") for s in steps)
    for script in ("reviewer/inbox_review.py", "reviewer/test_inbox_review.py"):
        assert f"python3 {script}" in runs and (here / script).is_file()
    assert ".github/scripts" not in runs
    push = next(s for s in steps if "git push" in s.get("run", ""))
    assert push["working-directory"] == "notes" and "pull -q --rebase" in push["run"]
    # The automation repo's own token only reads; writes go through the notes repo's token.
    assert wf["permissions"] == {"contents": "read"}
