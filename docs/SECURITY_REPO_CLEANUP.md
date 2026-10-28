# Security: Repo Cleanup After The Data Leak (P0-1)

> **STATUS (2026-10-28): tooling built and validated; the purge itself is NOT
> run on the real repo.** Steps marked **FOUNDER** are manual actions that only
> the repo owner / GitHub admin can do. Step 1 takes two minutes and stops the
> leak; do it first.

### Done vs to-do

| Item | Status |
|---|---|
| `devdata/`, `.dev/`, `*.archive(.gz)`, `*.bson(.gz)`, `.seed-credentials.local` ignored | Done (`.gitignore`) |
| Demo/seed passwords removed from tracked code, docs and tests | Done (seed generates random ones into git-ignored `backend/.seed-credentials.local`) |
| `scripts/purge-history.sh` built | Done |
| Script validated end-to-end on throwaway clones | Done: 169 MB to 99 MB `.git`; leaked paths unreachable from every ref; every branch tip tree byte-identical; `git fsck` clean |
| Make repo private, run the script, force-push, GitHub Support, re-clone everywhere, rotate credentials | **FOUNDER** |

### Runbook (what to actually do, in order)

1. **FOUNDER** - Step 1 below: list forks, make the repo private.
2. `pipx install git-filter-repo` (or `python3 -m pip install --user git-filter-repo`).
3. Commit everything first (clean tree required), then, **in your normal working copy on the laptop**
   (not a fresh clone from GitHub: the laptop holds unpushed work, including `blussit-v2`):
   ```bash
   cd ~/Documents/doorstep-vehicle-care-platform/doorstep-platform
   scripts/purge-history.sh          # asks you to type the repo folder name
   ```
   It writes a backup bundle to `~/blussit-purge-backups/` (this still contains the
   leaked data: keep it offline, delete it once the purge is verified), drops local
   checkpoint refs (`refs/codex/*`, `refs/pull/*`), runs `git filter-repo`, expires
   reflogs, gc, and prints the verification. It never pushes.
4. **FOUNDER** - allow force-push on protected branches briefly, then
   `git push --force origin main blussit-v2` and `git push --force --tags origin`
   (never `--mirror`). `frontend-work` and `nidhi-work` never held the leak: leave them.
5. **FOUNDER** - GitHub Support ("Removing sensitive data"): purge cached views,
   dangling objects and `refs/pull/*`; give both SHAs below. Delete/close PRs.
6. **FOUNDER** - every other clone, fork, CI cache and laptop: delete and re-clone.
   Never `git pull` into an old clone.
7. **FOUNDER** - Step 7 (credentials, accounts, legal notice).

Paths the script removes from all history (the only sensitive ones found in a scan
of all 1,323 historic paths): `devdata/` (database dump), `.dev/` (backend/frontend
logs, pids), plus defensive globs `*.archive(.gz)`, `*.bson(.gz)`,
`backend/deleted-*.json`, `.seed-credentials.local`. No `.env.production`, key or
certificate file was ever committed. A regex scan found only placeholder-looking
connection strings and key prefixes in docs/tests/examples; still run
`gitleaks detect --log-opts="--all"` after the purge.

Validation numbers (throwaway `git clone` of the real repo, run twice): `.git`
169,052 KiB to 99,228 KiB; history 70 to 69 commits (`b04ad32`, which only touched
`.dev/backend.log`, becomes empty and disappears); `git log --all -- devdata .dev`
empty; `111213a` and `b04ad32` no longer exist; `main` and `blussit-v2` tip tree
hashes identical to before (so the working tree content is byte-identical).

## What leaked

The GitHub repo **`blussit/blussit` is public**. Commit
`111213abd26d7ff6c7653496755f54e151212c51` ("pixels added", 6 Oct 2026, now the
tip of `origin/main`) added these files next to real code changes:

| File | What it holds |
|---|---|
| `devdata/blussit_dev.archive.gz` (84,589 bytes) | A dump of the local test database: **46 phone numbers, 2 Gmail addresses, 6 bcrypt password hashes**, bookings, OTP requests, payment orders, the WhatsApp outbox |
| `.dev/backend.log` (~71,000 lines), `.dev/frontend.log` | Local development server logs |
| `.dev/backend.pid`, `.dev/frontend.pid` | Process ids (harmless) |

The raw-file URL of the archive answered HTTP 200 (anyone could download it).

Your **local** `main` has one more commit that was **never pushed**:
`b04ad324b8bf5a4dba0a8a6f903d8d12108b12ac` ("new changes"), which only adds
13,857 more lines to `.dev/backend.log`. It must never be pushed.

Facts checked in the local clone:

- `origin/main` and `blussit-v2` (local and origin) contain the leaked commits.
  `frontend-work` and `nidhi-work` do not, so their history does not change.
- There are no tags.
- No other commit in the history touches `devdata/` or `.dev/`.
- The audit's search of the history found **no live API keys** (Razorpay,
  Meta, R2, MSG91, Mongo Atlas). The logs come from the local development
  setup, so they were signed with the **development** JWT secret, not the
  production one.

## Step 1: Make The Repo Private (Now)

First list the forks, because forks of a public repo **stay public** when the
original goes private:

```bash
gh api repos/blussit/blussit/forks --jq '.[].full_name'
```

Write the list down. Then make the repo private, either in GitHub
(**Settings → General → Danger Zone → Change Visibility → Make Private**) or
with the CLI:

```bash
gh repo edit blussit/blussit --visibility private --accept-visibility-change-consequences
```

Check it while logged out. The archive URL must now return 404:

```bash
curl -sI https://raw.githubusercontent.com/blussit/blussit/main/devdata/blussit_dev.archive.gz | head -1
```

## Step 2: Install git-filter-repo

Use any one of these:

```bash
pipx install git-filter-repo        # or: python3 -m pip install --user git-filter-repo
# Ubuntu/Debian: sudo apt install git-filter-repo     macOS: brew install git-filter-repo
git filter-repo --version
```

## Step 3: Purge `devdata/` And `.dev/` From All History

> Superseded by `scripts/purge-history.sh` (see the Runbook at the top). The
> manual mirror-clone commands below are kept as a fallback reference; Step 6
> is not needed when you run the script in place.

Work in a **fresh mirror clone** in a new folder, not in your normal working
copy:

```bash
cd ~ && mkdir -p blussit-purge && cd blussit-purge
git clone --mirror https://github.com/blussit/blussit.git blussit.git
cd blussit.git

git filter-repo --invert-paths \
  --path devdata/ \
  --path .dev/ \
  --path-glob '*.archive.gz'
```

`git filter-repo` keeps all the real code in `111213a` (config, payments,
Meta Pixel, frontend). It removes only those paths, from every commit, branch
and tag.

**Verify. All three commands must print nothing:**

```bash
git log --all --oneline -- devdata .dev
git rev-list --all --objects | grep -E ' (devdata|\.dev)(/|$)|\.archive\.gz' || true
git log --all --oneline | grep -E '^(111213a|b04ad32)' || true
```

Optionally, also run a secret scan on the cleaned history:

```bash
gitleaks detect --source . --log-opts="--all"
```

## Step 4: Force-Push All Branches And Tags

GitHub branch protection blocks force-pushes. If `main` is protected, first
allow force-pushes for a moment (**Settings → Branches**). Then:

```bash
git remote add origin https://github.com/blussit/blussit.git   # filter-repo removes it
git push --force --all origin
git push --force --tags origin
```

(Pushing `--mirror` would fail on GitHub's read-only `refs/pull/*`. That is
expected, which is why the commands above push branches and tags only.)

Turn branch protection back on afterwards. Then check GitHub itself:

```bash
git ls-remote https://github.com/blussit/blussit.git
# main must no longer point at 111213abd26d7ff6c7653496755f54e151212c51
```

## Step 5: Ask GitHub To Purge Cached Copies (And Deal With Forks)

A force-push does not delete the old commit on GitHub at once. It can still
be opened by its SHA, through pull-request refs or through forks.

1. Contact GitHub Support (<https://support.github.com/contact>, topic
   "Removing sensitive data"). Ask them to **purge cached views and
   unreachable objects** for `blussit/blussit`. Give them these SHAs:
   - `111213abd26d7ff6c7653496755f54e151212c51`
   - the paths `devdata/blussit_dev.archive.gz` and `.dev/`
2. Include the fork list from Step 1. Ask each fork owner to delete their
   fork (or re-fork from the cleaned repo), and ask Support about forks you
   can't reach.
3. Everyone with a clone (including the other developer's fork/clone) must
   **delete it and clone again**. An old clone still holds the files, and a
   push from it brings them back.

## Step 6: Clean This Laptop (Drop The Unpushed Commit `b04ad32`)

Do this only after Step 4. Run it in your normal working copy. These commands
do not touch the checked-out `blussit-v2` branch or your uncommitted changes:

```bash
cd ~/Documents/doorstep-vehicle-care-platform/doorstep-platform
git fetch origin --prune --force
git branch -f main origin/main        # main = cleaned history; b04ad32 is gone from main
git log --oneline main -3             # must NOT show b04ad32 or 111213a
git reflog expire --expire=now --all
git gc --prune=now
git log --all --oneline -- devdata .dev   # must print nothing
```

`b04ad32` only touched `.dev/backend.log`, so nothing of value is lost.

**Do not push before this step is done.** A plain `git push` of the old
local `main` would put both commits back.

## Step 7: Credential And Account Actions

| What | Action |
|---|---|
| **6 bcrypt password hashes** | Find those 6 accounts (by the emails/phones in the dump) in **production**. Force a password reset (`must_change_password`) and revoke their sessions. bcrypt is slow to crack, but weak passwords fall. Also treat their old passwords as known anywhere else they were used. |
| 46 phone numbers, 2 Gmail addresses, bookings, payment orders, WhatsApp outbox | Personal data was exposed. **Get legal advice today** on notification duties (India's DPDP Act 2023 breach notice to the Data Protection Board and the affected people; CERT-In's 6-hour incident reporting for data leaks). |
| OTP requests | Already expired. No action, but they also reveal phone numbers. |
| JWT secret | The dev logs came from the **local development environment** (dev JWT secret). The production `JWT_SECRET_KEY` is not in the repo history. **If there is any doubt, rotate it** in `backend/.env.production` and redeploy. This logs everyone out once. |
| API keys (Razorpay, Meta, R2, MSG91, Google, Atlas) | The audit found **no live keys** in the history. Still, rotate any key you have ever pasted into a committed file or a log. |

## Step 8: Prevention (Already Done In The Code)

- The root `.gitignore` now ignores `.dev/`, `devdata/`, `*.archive`,
  `*.archive.gz`, `*.bson(.gz)`, `*.log`, `*.pid` and every `.env*` except the
  `*.example` files. `backend/.gitignore` repeats the env rules.
- **Commit `backend/.gcloudignore`** (it is still untracked). Without it,
  `gcloud run deploy --source backend` would upload `backend/.env.production`
  to Cloud Build. `scripts/deploy-gcp.sh` now refuses to deploy without it.
- Before every push, run `git status` and check the file list. Never `git add -A`
  from the repo root while `devdata/` or a dump exists.
- Turn on GitHub secret scanning and push protection (repo **Settings → Code
  security**).
