# Blussit — Local Setup On Windows

This guide gets the whole Blussit platform running on your Windows PC — the
website, the customer app, and the captain, manager and admin portals — with
a **local test database**. Nothing you do here touches the live site, real
customers, real payments or real WhatsApp messages.

What you'll have at the end:

| What | Where |
|---|---|
| Website + all portals | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| API docs (try every endpoint) | http://localhost:8000/api/docs |
| Database | MongoDB in Docker, on port `27099`, database `blussit_dev` |

Local shortcuts that are switched on automatically:

- **Every OTP is `123456`** — no SMS or WhatsApp is ever sent.
- WhatsApp messages are written to the `whatsapp_outbox` collection instead of being sent.
- Uploaded photos are saved in `backend\uploads`.
- A small yellow **"LOCAL · test database · OTP 123456"** badge shows at the top of every page, so you always know you're not on the live site.

Time needed: about 30–45 minutes the first time (mostly downloads), then about 1 minute a day.

---

## 1. Install The Tools (One Time)

Install these in order. Accept the default options unless a step says otherwise.

| Tool | Version | Download | Check it worked (in a new PowerShell window) |
|---|---|---|---|
| Git | latest | https://git-scm.com/download/win | `git --version` |
| Python | **3.12.x** | https://www.python.org/downloads/windows/ | `py -3.12 --version` |
| Node.js | **22 LTS** (20.19 or newer) | https://nodejs.org/en/download | `node -v` and `npm -v` |
| Docker Desktop | latest | https://www.docker.com/products/docker-desktop/ | `docker --version` |
| VS Code (optional) | latest | https://code.visualstudio.com/ | — |

Notes:

- **Python:** on the first installer screen tick **"Add python.exe to PATH"**. Use 3.12 — it's what the project is tested with.
- **Docker Desktop:** when asked, choose **"Use WSL 2"**. Restart Windows if it asks. Open Docker Desktop once and wait until it says **"Engine running"** (green, bottom-left). Docker must be running every time you work on the project.
- Open PowerShell with: Start menu → type **PowerShell** → Windows PowerShell. All commands below are for PowerShell.

---

## 2. Get The Project

If you received the code through Git:

```powershell
cd $HOME\Documents
git clone <repository-url> doorstep-platform
cd doorstep-platform
```

If you received a zip, unzip it to `Documents\doorstep-platform` and `cd` into that folder.

Every command below starts from this project folder (the one that contains
`backend`, `frontend` and this file). If you get lost:

```powershell
cd $HOME\Documents\doorstep-platform
```

---

## 3. Start The Database (One Time)

The app needs MongoDB running as a "replica set" (a mode it uses for safe
money and booking updates). Docker does this for you.

**3.1 Create the database container** (copy all lines together):

```powershell
docker run -d --name blussit-dev-mongo `
  -p 127.0.0.1:27099:27017 `
  -v blussit_dev_mongo:/data/db `
  --ulimit nofile=64000:64000 `
  --restart unless-stopped `
  mongo:7 --replSet rs0 --bind_ip_all --wiredTigerCacheSizeGB 0.5
```

The first time this downloads MongoDB (about 1 minute).

**3.2 Switch on replica-set mode** (wait about 5 seconds after 3.1):

```powershell
docker exec blussit-dev-mongo mongosh --quiet --eval "rs.initiate({_id:'rs0',members:[{_id:0,host:'127.0.0.1:27017'}]})"
```

You should see `{ ok: 1 }`.

**3.3 Check it:**

```powershell
docker exec blussit-dev-mongo mongosh --quiet --eval "rs.status().ok"
```

It must print `1`. The data lives in a Docker volume called `blussit_dev_mongo`,
so it survives restarts.

---

## 4. Set Up The Backend (One Time)

**4.1 Create a Python environment and install packages:**

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

When the environment is active your prompt starts with `(.venv)`.

> **"running scripts is disabled on this system"?** Run this once, then try
> `.\.venv\Scripts\Activate.ps1` again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

**4.2 Create your settings file** from the safe template:

```powershell
Copy-Item .env.development.example .env.development
```

That's all you need. Optional extras inside `backend\.env.development`
(open it in VS Code or Notepad):

| Setting | What it does | Without it |
|---|---|---|
| `GOOGLE_MAPS_BROWSER_KEY`, `GOOGLE_MAPS_SERVER_KEY` | The map / location pin on the booking page and road distance | Map doesn't load; ask the project owner for the **dev** keys |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Online payments in **test mode** (keys start with `rzp_test_`) | Online payment is hidden locally; cash bookings work |
| `GOOGLE_OAUTH_CLIENT_ID` | "Continue with Google" login | Phone + OTP login works anyway |

> **Never** put live keys (anything starting `rzp_live_`, or the production
> database address `mongodb+srv://…`) into `.env.development`. The app refuses
> to switch on the OTP shortcut unless the database is on your own computer.

**4.3 Load data into the database — pick ONE:**

**Option A — Start fresh** (demo services, plans, staff and three test customers):

```powershell
python -m app.scripts.seed_dev
```

**Option B — Exactly the same data as the project owner** (societies,
residents, bookings, plans, schedules…). Ask for the file
`blussit_dev.archive.gz`, put it in the project's `devdata` folder
(create the folder if needed), then from the **project folder** (not `backend`):

```powershell
cd ..
docker cp .\devdata\blussit_dev.archive.gz blussit-dev-mongo:/tmp/blussit_dev.archive.gz
docker exec blussit-dev-mongo mongorestore --archive=/tmp/blussit_dev.archive.gz --gzip --drop
cd backend
```

**4.4 Start the backend:**

```powershell
uvicorn app.main:app --reload --port 8000
```

Wait for `Application startup complete.` You'll also see a line saying
**"DEV TOOLS ON (local test database blussit_dev): every OTP is 123456"** —
that confirms you're on the local database. Leave this window open; it reloads
by itself when you change backend code. Check it at http://localhost:8000/api/docs.

---

## 5. Set Up The Website (One Time)

Open a **second** PowerShell window (the backend keeps running in the first):

```powershell
cd $HOME\Documents\doorstep-platform\frontend
Copy-Item .env.development.example .env.development
npm ci
npm run dev
```

`npm ci` takes a few minutes the first time. When you see
`Local: http://localhost:5173/`, open **http://localhost:5173** in your browser.

> Always use **localhost**, not `127.0.0.1` — the backend only accepts the
> website from `http://localhost:5173`.

---

## 6. Log In And Try Every Role

| Role | Where | Login |
|---|---|---|
| Admin | http://localhost:5173/login | `admin@doorstepvehiclecare.in` / `Admin@12345` |
| Manager | same | `manager.indore@doorstepvehiclecare.in` / `Manager@12345` |
| Captain | same (use a phone-size window or your phone) | `captain.indore@doorstepvehiclecare.in` / `Captain@12345` |
| Customer — new | same | `9000000001`, OTP `123456` |
| Customer — has a plan | same | `9000000002`, OTP `123456` |
| Customer — has past washes | same | `9000000003`, OTP `123456` |

Only with Option B (the owner's data): society resident **Neha Verma**
`9821315703` + OTP `123456`, and a second captain **Amit Yadav**
`9876512340` / `Captain@12345`.

Any other 10-digit mobile number also works with OTP `123456` — it creates a
new customer, exactly like a real first booking.

Tip: to see the phone layout on a computer, press **F12** in Chrome, then
**Ctrl+Shift+M** and pick a phone size.

---

## 7. Every Day After That

1. Open **Docker Desktop** and wait for "Engine running" (the database starts by itself).
2. Backend — PowerShell window 1:
   ```powershell
   cd $HOME\Documents\doorstep-platform\backend
   .\.venv\Scripts\Activate.ps1
   uvicorn app.main:app --reload --port 8000
   ```
3. Website — PowerShell window 2:
   ```powershell
   cd $HOME\Documents\doorstep-platform\frontend
   npm run dev
   ```
4. Open http://localhost:5173.

To stop: press **Ctrl+C** in both windows. The database can keep running, or
stop it with `docker stop blussit-dev-mongo` (start again with
`docker start blussit-dev-mongo`).

After pulling new code, run `pip install -r requirements.txt` (backend, with
`.venv` active) and `npm ci` (frontend) in case packages changed.

---

## 8. Useful Extras

**Wipe the local data and start again:**

```powershell
docker exec blussit-dev-mongo mongosh --quiet --eval "db.getSiblingDB('blussit_dev').dropDatabase()"
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.scripts.seed_dev
```

**Share your data with a teammate** (makes the `blussit_dev.archive.gz` used in Option B), from the project folder:

```powershell
New-Item -ItemType Directory -Force devdata | Out-Null
docker exec blussit-dev-mongo mongodump --db blussit_dev --archive=/tmp/blussit_dev.archive.gz --gzip
docker cp blussit-dev-mongo:/tmp/blussit_dev.archive.gz .\devdata\blussit_dev.archive.gz
```

The `devdata` folder is ignored by Git — send the file directly (it's small).

**Run the backend tests** (about 6–10 minutes; they use their own throwaway
database and never touch `blussit_dev`):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

**Look inside the database** with MongoDB Compass (https://www.mongodb.com/products/compass):
connection string `mongodb://127.0.0.1:27099/?replicaSet=rs0&directConnection=true`, database `blussit_dev`.

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `docker` is not recognized / "cannot connect to the Docker daemon" | Open Docker Desktop and wait for "Engine running". Restart PowerShell. |
| `docker run` says the name `blussit-dev-mongo` is already in use | The container exists already — just `docker start blussit-dev-mongo`. |
| Backend error mentioning `replica set`, `not primary` or `ServerSelectionTimeoutError` | Run step 3.2 again, then `docker exec blussit-dev-mongo mongosh --quiet --eval "rs.status().ok"` must print `1`. |
| `Refusing to seed: this only runs against the LOCAL test database` | `backend\.env.development` is missing or edited. Copy it again from `.env.development.example` (step 4.2). |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then activate again. |
| `UnicodeEncodeError` while seeding (₹ symbol) | Run `$env:PYTHONUTF8 = "1"` in that window, then the command again. |
| Website loads but every action fails / "Can't reach the server" | The backend isn't running (step 4.4), or you opened `127.0.0.1:5173` — use **http://localhost:5173**. |
| Port 8000 or 5173 "already in use" | Another copy is still running — close the old PowerShell window, or restart the PC. |
| Map on the booking page is blank | Add the Google Maps dev keys to `backend\.env.development` (ask the owner), then restart the backend. |
| `npm ci` fails | Delete the `frontend\node_modules` folder and run `npm ci` again. Make sure `node -v` is 20.19 or newer. |
| Login says the OTP is wrong | Use `123456`. If the backend's start-up log doesn't show "DEV TOOLS ON", your settings file isn't the development one. |
| Docker uses too much memory | Docker Desktop → Settings → Resources → lower the memory, or `docker stop blussit-dev-mongo` when you're done. |

---

## Ground Rules For This Project

- Work only against the **local** database. The production settings file
  (`backend\.env.production`) is never shared and never needed for local work.
- Never commit `.env.development`, `.env.production` or `devdata\` — Git ignores them already.
- Deploying to the live site is done by the project owner only.

macOS / Linux: `scripts/dev.sh up` runs all of the above in one command
(see the top of that script for `down`, `seed`, `reset` and `test`).
