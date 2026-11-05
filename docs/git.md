# The git remote

git-sync works with any remote: GitHub, Gitea, Forgejo, GitLab, a bare repo on
a NAS. Make the repository **private**: it holds every note in plain text.

| Auth | Set | Notes |
|---|---|---|
| HTTPS token | `GIT_TOKEN_FILE=/secrets/git/token`, the token in `secrets/git/token` (mode 600) | A fine-grained token limited to this one repository, contents read and write. Read at every git call, never put in the URL. |
| GitHub App | profile `github-app`, `GIT_TOKEN_FILE=/run/git/token`, `GH_APP_*`, the app's key in `secrets/git/github-app.pem` | Best on GitHub: a 1-hour token narrowed to the one repo, minted by a sidecar that is the only container holding the key. The app needs Contents read and write, installed on this repository only. |
| SSH | `GIT_SSH_KEY=/secrets/git/id_ed25519`, `GIT_SSH_KNOWN_HOSTS=/secrets/git/known_hosts` | A deploy key with write access. Host keys are checked against the file, never trusted on first use. |

## File ownership

git-sync runs as uid 1000. On a Linux host, the files under `secrets/git/` (the
token, the SSH key, `known_hosts`) must be readable by that uid:
`sudo chown 1000 secrets/git/*` and keep them mode 600. (On macOS, Docker
Desktop maps ownership for you.) git-sync copies an SSH key to a private file
before using it, so a group-readable mount works too.

## What git-sync refuses

Push access to the notes repo is the owner's access: a commit lands in the
folder with no tiers. Give it out accordingly.

- **Mass deletions.** More than 20 notes gone in one round (`NOTES_MAX_DELETIONS`)
  is not committed: a wiped volume or a LiveSync reset must not erase the repo.
  It logs `status=refused` every round until you decide (docs/operations.md).
- **Hidden paths, symlinks, submodules from the remote.** An
  `.obsidian/plugins/*/main.js` is code that Obsidian runs. A remote carrying
  any hidden path (but a root `.gitignore` or `.gitattributes`), a symlink or a
  submodule is not merged: `status=refused reason=unsafe_remote` until it's
  reverted there.
- **Obsidian's app settings.** `.obsidian/` and `.trash/` never leave the
  machine.

## Protect the branch

On the host, forbid force-pushes and deletion of the branch git-sync pushes to.
An agent with a bad day can then only add commits, which git-sync merges.
