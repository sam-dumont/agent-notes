#!/bin/sh
# git-sync: keeps the notes folder that the LiveSync daemon mirrors in step
# with a git remote (any host: GitHub, Gitea, Forgejo, GitLab, a bare repo).
#
# Every INTERVAL seconds: commit what the devices and the MCP server changed,
# merge what agents pushed to the remote, push the result. The work tree is
# the folder the daemon watches, so a merge reaches the phones through
# CouchDB with no extra step. The git dir lives outside it (GIT_DIR), so .git
# never reaches CouchDB or the devices.
#
# Credentials, one of:
#   GIT_TOKEN_FILE  a file holding an HTTPS token (the GitHub App minter
#                   writes one every 30 min; a fine-grained PAT works too).
#                   Read at every git call, never put in the remote URL.
#   GIT_SSH_KEY     an SSH private key file, with GIT_SSH_KNOWN_HOSTS (a
#                   known_hosts file: host keys are checked, never trusted on
#                   first use). ssh needs a passwd entry for the uid it runs
#                   as: the deployments mount sync/git-sync.passwd.
#   nothing         a local path or a remote that needs no auth (tests).
#
# Every outcome is one `agent-notes-git: status=...` line, for alerting:
#   status=ok        in step with the remote
#   status=busy      a file changed under a merge; retried next round
#   status=conflict  both sides edited the same lines: the markers are
#                    committed, so the note shows them on every device
#   status=refused   reason=mass_delete: more than MAX_DELETIONS files gone in
#                    one round, not committed (a wiped volume or a LiveSync
#                    reset must not erase the repo); reason=unsafe_remote: the
#                    remote carries hidden paths, symlinks or submodules, not
#                    merged; see docs/operations.md
#   status=error     fetch or push failed
set -u

: "${GIT_DIR:?}" "${GIT_WORK_TREE:?}" "${NOTES_REMOTE:?}"
BRANCH="${NOTES_BRANCH:-main}"
INTERVAL="${NOTES_INTERVAL:-30}"
MAX_DELETIONS="${NOTES_MAX_DELETIONS:-20}"
export GIT_DIR GIT_WORK_TREE

log() { echo "agent-notes-git: $*"; }

if [ -n "${GIT_TOKEN_FILE:-}" ]; then
  # The helper reads the file at each call, so a rotated token is picked up
  # without a restart. x-access-token is GitHub's user for app tokens; other
  # hosts ignore the user name for token auth or take GIT_TOKEN_USER.
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper
  export GIT_CONFIG_VALUE_0="!f() { echo username=${GIT_TOKEN_USER:-x-access-token}; echo \"password=\$(cat \"$GIT_TOKEN_FILE\")\"; }; f"
elif [ -n "${GIT_SSH_KEY:-}" ]; then
  : "${GIT_SSH_KNOWN_HOSTS:?GIT_SSH_KNOWN_HOSTS is required with GIT_SSH_KEY: host keys are never trusted on first use}"
  # ssh refuses a key others can read, and a mounted secret often is (a
  # Kubernetes Secret gets the pod's group): use a private copy.
  key="${TMPDIR:-/tmp}/git-ssh-key"
  (umask 077 && cat "$GIT_SSH_KEY" > "$key")
  export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o UserKnownHostsFile=$GIT_SSH_KNOWN_HOSTS -o StrictHostKeyChecking=yes -o BatchMode=yes"
fi

# What the remote may not bring into the folder, whoever pushed it:
#   - hidden paths (.obsidian/plugins/*/main.js is code that Obsidian, and
#     Obsidian in a browser, runs; .git* files configure git itself), except a
#     root .gitignore or .gitattributes;
#   - symlinks (they point out of the folder) and submodules.
# Prints the offending paths; empty means safe. Fails closed on anything it
# can't parse.
unsafe_remote() {
  git ls-tree -r -z --full-tree "origin/$BRANCH" | tr '\0' '\n' | awk -F'\t' '
    NF != 2 { print "unparsable entry"; next }
    {
      split($1, meta, " "); mode = meta[1]; p = $2; bad = 0
      if (mode == "120000" || mode == "160000") bad = 1
      if (p != ".gitignore" && p != ".gitattributes") {
        n = split(p, parts, "/")
        for (i = 1; i <= n; i++) if (substr(parts[i], 1, 1) == ".") bad = 1
      }
      if (bad) print p
    }' | head -20
}

refuse_unsafe_remote() {
  bad=$(unsafe_remote)
  [ -z "$bad" ] && return 1
  log "status=refused reason=unsafe_remote files=\"$(echo "$bad" | tr '\n' ' ' | sed 's/ $//')\""
  return 0
}

# Take the remote's history as ours without losing the work tree: what's on disk
# stays (it's newer), files only the remote has are written out.
adopt_remote() {
  git reset -q --mixed "origin/$BRANCH"
  git ls-files -z --deleted | xargs -0 -r git checkout -q --
}

# First start on an empty volume: adopt the remote's history. `reset --mixed`
# keeps whatever the daemon already wrote (a device's copy is newer than the
# repo's and wins in the first commit); files only the repo has are written
# out, never read as deletions. A bootstrap only ever adds files.
bootstrap() {
  [ -f "$GIT_DIR/HEAD" ] && return 0
  mkdir -p "$GIT_WORK_TREE"
  git init -q -b "$BRANCH"
  # Symlinks become plain files even if one gets past unsafe_remote.
  git config core.symlinks false
  git remote add origin "$NOTES_REMOTE"
  if ! git fetch -q origin; then
    log "status=error reason=fetch phase=bootstrap"
    # Empty it rather than remove it: GIT_DIR is often a mount point.
    find "$GIT_DIR" -mindepth 1 -delete
    return 1
  fi
  if git rev-parse -q --verify "origin/$BRANCH" >/dev/null; then
    if refuse_unsafe_remote; then find "$GIT_DIR" -mindepth 1 -delete; return 1; fi
    adopt_remote
  fi
  log "status=bootstrapped head=$(git rev-parse --short HEAD 2>/dev/null || echo none)"
}

sync_once() {
  git add -A
  deletions=$(git diff --cached --name-only --diff-filter=D | wc -l | tr -d ' ')
  if [ "$deletions" -gt "$MAX_DELETIONS" ]; then
    git reset -q
    log "status=refused reason=mass_delete files=$deletions max=$MAX_DELETIONS"
    return 1
  fi
  if ! git diff --cached --quiet; then
    changed=$(git diff --cached --name-only | wc -l | tr -d ' ')
    git commit -q -m "notes: $changed file(s) from devices" -m "Committed by agent-notes git-sync."
  fi

  if ! git fetch -q origin; then
    log "status=error reason=fetch"
    return 1
  fi
  if ! git rev-parse -q --verify HEAD >/dev/null; then
    # Nothing committed here yet: take the remote's history if it has one.
    if git rev-parse -q --verify "origin/$BRANCH" >/dev/null; then
      refuse_unsafe_remote && return 1
      adopt_remote
    fi
    return 0
  fi

  if git rev-parse -q --verify "origin/$BRANCH" >/dev/null && ! git merge-base --is-ancestor "origin/$BRANCH" HEAD; then
    # Nothing merged, nothing pushed while the remote carries what it may not.
    refuse_unsafe_remote && return 1
    if ! out=$(git merge -q --no-edit --allow-unrelated-histories "origin/$BRANCH" 2>&1); then
      if git diff --name-only --diff-filter=U | grep -q .; then
        files=$(git diff --name-only --diff-filter=U | tr '\n' ' ' | sed 's/ $//')
        git add -A
        git commit -q --no-edit -m "notes: merge conflict, fix by hand: $files"
        log "status=conflict files=\"$files\""
      else
        # The daemon wrote a file the merge needs between our commit and
        # the merge: nothing merged, nothing lost.
        git merge --abort 2>/dev/null
        log "status=busy reason=\"$(echo "$out" | head -1)\""
        return 0
      fi
    fi
  fi

  if [ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$BRANCH" 2>/dev/null)" ]; then
    if ! git push -q origin "HEAD:$BRANCH"; then
      log "status=error reason=push"
      return 1
    fi
  fi
  log "status=ok head=$(git rev-parse --short HEAD)"
}

until bootstrap; do sleep "$INTERVAL"; done
# Obsidian keeps its app settings in the folder: device settings, not notes,
# and never pushed. .trash/ is where it puts deleted notes; git history
# already has them.
printf '%s\n' .obsidian/ .OBSIDIANTEST .trash/ >"$GIT_DIR/info/exclude"
while :; do
  sync_once || true
  sleep "$INTERVAL"
done
