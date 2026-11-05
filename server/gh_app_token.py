"""Keep a fresh GitHub App installation token in a file, for git-sync.

Optional sidecar next to git-sync, for a GitHub remote. The app (Contents
read and write, installed on the notes repository only) signs a 9-minute JWT
with its private key, trades it for a 1-hour installation token narrowed to
that one repository, and writes it to TOKEN_FILE (a memory-backed volume
shared with git-sync) every REFRESH_SECONDS. git-sync's credential helper
reads the file: it never sees the private key, and a leaked token is dead
within the hour.

Lines are `gh-app-token: status=...`, never the token.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import jwt

API = "https://api.github.com"


def app_jwt(app_id: str, private_key: str, now: int) -> str:
    # iat 60 s in the past absorbs clock drift (GitHub's own advice); 9 min,
    # under the 10-minute maximum.
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, algorithm="RS256")


def installation_token(app_id: str, installation_id: str, private_key: str) -> tuple[str, str]:
    req = urllib.request.Request(
        f"{API}/app/installations/{installation_id}/access_tokens",
        method="POST",
        headers={
            "Authorization": f"Bearer {app_jwt(app_id, private_key, int(time.time()))}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-notes-gh-app-token",
        },
        # Narrowed to the one repo and the one permission git-sync needs,
        # whatever the installation grows to later.
        data=json.dumps({"repositories": [os.environ["GH_APP_REPO"]], "permissions": {"contents": "write"}}).encode(),
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    return body["token"], body["expires_at"]


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def main() -> None:
    env = os.environ
    app_id, inst = env["GH_APP_ID"], env["GH_APP_INSTALLATION_ID"]
    key = Path(env.get("GH_APP_KEY_FILE", "/etc/gh-app/private-key.pem")).read_text()
    out = Path(env.get("TOKEN_FILE", "/run/gh/token"))
    refresh = int(env.get("REFRESH_SECONDS", "1800"))
    while True:
        try:
            token, expires = installation_token(app_id, inst, key)
            write_atomic(out, token)
            print(f"gh-app-token: status=ok expires={expires}", flush=True)
            time.sleep(refresh)
        except Exception as e:  # noqa: BLE001 - logged and retried; git-sync keeps the last token
            print(f"gh-app-token: status=error reason={type(e).__name__}: {str(e)[:200]!r}", file=sys.stdout, flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
