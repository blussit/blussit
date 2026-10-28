#!/usr/bin/env bash
# Purge leaked dev-database dumps and dev logs from ALL git history.
#
#   scripts/purge-history.sh                 # run inside the real repo (interactive)
#   scripts/purge-history.sh --repo <path>   # run against another clone (used for the dry run)
#
# What it does, in order:
#   1. refuses unless git-filter-repo is installed and the working tree is clean
#   2. makes you type the repo folder name (PURGE_CONFIRM=<name> skips the prompt - test use only)
#   3. writes a full backup bundle OUTSIDE the repo (that bundle still contains the
#      leaked data: keep it offline / encrypted and delete it once the purge is verified)
#   4. removes the sensitive paths from every commit on every ref
#   5. expires reflogs and garbage-collects, then verifies and prints the next steps
#
# It never pushes. The force-push commands are printed for the founder to run.
set -euo pipefail

# Paths removed from history. Keep in sync with docs/SECURITY_REPO_CLEANUP.md.
PURGE_PATHS=(devdata/ .dev/ .seed-credentials.local backend/.seed-credentials.local)
PURGE_GLOBS=('*.archive.gz' '*.archive' '*.bson' '*.bson.gz' 'backend/deleted-*.json')
# Local-only refs that carry old trees (editor/agent checkpoints, GitHub PR refs).
DROP_REF_PREFIXES=(refs/codex/ refs/pull/ refs/original/)

say() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

REPO=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="${2:?--repo needs a path}"; shift 2 ;;
    --backup-dir) BACKUP_DIR="${2:?}"; shift 2 ;;
    -h|--help) sed -n 2,16p "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$REPO" ]] || REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$REPO" && git rev-parse --show-toplevel)"
NAME="$(basename "$REPO")"
BACKUP_DIR="${BACKUP_DIR:-$HOME/blussit-purge-backups}"
cd "$REPO"

# --- 1. preconditions ------------------------------------------------------
if ! git filter-repo --version >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: git-filter-repo is not installed. Install it with any one of:
  pipx install git-filter-repo
  python3 -m pip install --user git-filter-repo
  sudo apt install git-filter-repo        (Ubuntu/Debian)    brew install git-filter-repo (macOS)
then check:  git filter-repo --version
EOF
  exit 1
fi
[[ -z "$(git status --porcelain)" ]] || die "working tree is not clean (commit or stash first; ignored files are fine)."
case "$BACKUP_DIR/" in "$REPO"/*) die "backup dir must be outside the repo: $BACKUP_DIR" ;; esac

# --- 2. confirmation -------------------------------------------------------
say "About to REWRITE ALL HISTORY of: $REPO"
echo "Paths removed from every commit: ${PURGE_PATHS[*]} ${PURGE_GLOBS[*]}"
if [[ "${PURGE_CONFIRM:-}" != "$NAME" ]]; then
  read -r -p "Type the repo folder name ($NAME) to continue: " typed
  [[ "$typed" == "$NAME" ]] || die "name did not match - nothing was changed."
fi

# --- 3. record state + backup ----------------------------------------------
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
BUNDLE="$BACKUP_DIR/$NAME-$STAMP.bundle"
say "Backup bundle -> $BUNDLE"
git bundle create "$BUNDLE" --all >/dev/null 2>&1
git bundle verify "$BUNDLE" >/dev/null 2>&1 || die "backup bundle failed verification."
chmod 600 "$BUNDLE"
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
SIZE_BEFORE="$(du -sk .git | cut -f1)"
BEFORE_TREES="$(mktemp)"; AFTER_TREES="$(mktemp)"
trap 'rm -f "$BEFORE_TREES" "$AFTER_TREES"' EXIT
# Tip tree hash of every local branch/tag (filter-repo drops origin/* remote-tracking refs): unchanged trees = identical content.
tip_trees() { git for-each-ref --format='%(refname) %(objecttype) %(objectname)' refs/heads refs/tags |
  while read -r ref type sha; do
    [[ "$type" == commit ]] || continue
    printf '%s %s\n' "$ref" "$(git rev-parse "$sha^{tree}")"
  done | sort; }
tip_trees > "$BEFORE_TREES"
echo "Repo size before: ${SIZE_BEFORE} KiB (.git)"

# --- 4. rewrite ------------------------------------------------------------
say "Dropping local-only checkpoint refs"
for prefix in "${DROP_REF_PREFIXES[@]}"; do
  git for-each-ref --format='%(refname)' "$prefix" | while read -r r; do git update-ref -d "$r"; done
done

say "git filter-repo"
ARGS=(--force --invert-paths)
for p in "${PURGE_PATHS[@]}"; do ARGS+=(--path "$p"); done
for g in "${PURGE_GLOBS[@]}"; do ARGS+=(--path-glob "$g"); done
git filter-repo "${ARGS[@]}"

[[ -z "$ORIGIN_URL" ]] || git remote add origin "$ORIGIN_URL"   # filter-repo removes it

say "Expiring reflogs + gc"
git reflog expire --expire=now --all
git gc --prune=now --aggressive -q
SIZE_AFTER="$(du -sk .git | cut -f1)"

# --- 5. verification ---------------------------------------------------------
say "Verification"
FAIL=0
leftover="$(git log --all --oneline -- devdata .dev)"
[[ -z "$leftover" ]] && echo "OK  git log --all -- devdata .dev is empty" || { echo "FAIL commits still touch devdata/.dev"; FAIL=1; }
objs="$(git rev-list --all --objects | grep -E ' (devdata|\.dev)(/|$)|\.archive(\.gz)?$|\.bson(\.gz)?$' || true)"
[[ -z "$objs" ]] && echo "OK  no devdata/.dev/archive/bson object reachable from any ref" || { echo "FAIL objects still reachable"; FAIL=1; }
for sha in 111213abd26d7ff6c7653496755f54e151212c51 b04ad324b8bf5a4dba0a8a6f903d8d12108b12ac; do
  if git cat-file -e "$sha^{commit}" 2>/dev/null; then echo "FAIL old commit $sha still exists"; FAIL=1; fi
done
[[ $FAIL -eq 0 ]] && echo "OK  old commits 111213a / b04ad32 are gone (hashes were rewritten)"
tip_trees > "$AFTER_TREES"
if diff "$BEFORE_TREES" "$AFTER_TREES" >/dev/null; then
  echo "OK  every branch/tag tip has a byte-identical tree to before"
else
  echo "NOTE tip trees differ from before (expected ONLY if a tip itself contained the purged paths):"
  diff "$BEFORE_TREES" "$AFTER_TREES" | sed -n 1,20p || true
fi
git fsck --no-progress >/dev/null 2>&1 && echo "OK  git fsck clean" || { echo "FAIL git fsck"; FAIL=1; }
echo "Repo size: before ${SIZE_BEFORE} KiB -> after ${SIZE_AFTER} KiB"
echo "Backup bundle (still contains the leaked data - keep offline, delete after verifying): $BUNDLE"
[[ $FAIL -eq 0 ]] || die "verification failed - DO NOT PUSH. Restore from the bundle: git clone $BUNDLE restored"

# --- 6. what to do next -------------------------------------------------------
cat <<EOF

== Next steps (nothing has been pushed)
1. Make the GitHub repo private first (docs/SECURITY_REPO_CLEANUP.md step 1), then allow
   force-push on protected branches for a moment.
2. Force-push (from $REPO):
     git push --force origin main blussit-v2        # the branches that carried the leak
     # other remote branches (frontend-work, nidhi-work) never contained it: leave them alone
     git push --force --tags origin
   (do NOT use --mirror: GitHub's read-only refs/pull/* would be rejected.)
3. Check:  git ls-remote origin     # main/blussit-v2 must no longer show 111213a or b04ad32
4. Every other clone/fork (teammates, CI caches, other laptops): DELETE it and re-clone.
   An old clone still holds the data and a push from it brings the data back.
   Fork owners: delete the fork and re-fork. Do not "git pull" into an old clone.
5. GitHub still serves old commits by SHA and via refs/pull/* until it garbage-collects:
   contact GitHub Support (topic "Removing sensitive data") asking them to purge cached
   views, dangling objects and PR refs; give them the SHAs 111213abd26d7ff6c7653496755f54e151212c51
   and b04ad324b8bf5a4dba0a8a6f903d8d12108b12ac. Close/delete open PRs whose refs/pull/* hold old commits.
6. Rotate credentials / reset the 6 exposed accounts (checklist in docs/SECURITY_REPO_CLEANUP.md step 7).
EOF
