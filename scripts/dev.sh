#!/usr/bin/env bash
# Local development environment — test database, test credentials, fixed OTP.
#
#   scripts/dev.sh up       start Mongo + backend (auto-reload) + website
#   scripts/dev.sh down     stop all three
#   scripts/dev.sh status   what's running
#   scripts/dev.sh logs     follow backend + website logs
#   scripts/dev.sh seed     (re)create the test accounts
#   scripts/dev.sh reset    wipe the LOCAL test database and seed it fresh
#   scripts/dev.sh test     run the backend test suite (separate throwaway DB)
#
# Website http://localhost:5173 · API docs http://localhost:8000/api/docs
# Uses backend/.env.development only. Production credentials
# (backend/.env.production) are never read by this script.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.dev"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
MONGO_CONTAINER="blussit-dev-mongo"
MONGO_VOLUME="blussit_dev_mongo"
MONGO_PORT=27099
DEV_DB="blussit_dev"
LOCAL_MONGO_URI="mongodb://127.0.0.1:${MONGO_PORT}/?replicaSet=rs0&directConnection=true"
mkdir -p "$STATE"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f "$BACKEND/.env.development" ]] || fail "backend/.env.development is missing"

# Docker Desktop's context is sometimes down while the system daemon works.
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then DOCKER=(docker --context default); fi
"${DOCKER[@]}" info >/dev/null 2>&1 || fail "Docker isn't running"

mongosh_eval() { "${DOCKER[@]}" exec "$MONGO_CONTAINER" mongosh --quiet --eval "$1"; }

start_mongo() {
  if "${DOCKER[@]}" ps --format '{{.Names}}' | grep -qx "$MONGO_CONTAINER"; then
    :
  elif "${DOCKER[@]}" ps -a --format '{{.Names}}' | grep -qx "$MONGO_CONTAINER"; then
    "${DOCKER[@]}" start "$MONGO_CONTAINER" >/dev/null
  else
    say "Creating local Mongo ($MONGO_CONTAINER, data in volume $MONGO_VOLUME)…"
    "${DOCKER[@]}" run -d --name "$MONGO_CONTAINER" --ulimit nofile=64000:64000 \
      -p "127.0.0.1:${MONGO_PORT}:27017" -v "${MONGO_VOLUME}:/data/db" mongo:7 \
      --replSet rs0 --bind_ip_all --setParameter diagnosticDataCollectionEnabled=false --wiredTigerCacheSizeGB 0.5 >/dev/null
  fi
  for _ in $(seq 1 30); do mongosh_eval "db.runCommand({ping:1}).ok" >/dev/null 2>&1 && break; sleep 1; done
  if ! mongosh_eval "rs.status().ok" >/dev/null 2>&1; then
    mongosh_eval 'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27017"}]})' >/dev/null
    sleep 3
  fi
  say "Mongo   ✓  127.0.0.1:${MONGO_PORT} (database $DEV_DB)"
}

ensure_venv() {
  if [[ ! -x "$VENV/bin/python" ]]; then
    say "Creating backend/.venv…"
    python3 -m venv "$VENV"
  fi
  if [[ ! -f "$VENV/.installed" || "$BACKEND/requirements.txt" -nt "$VENV/.installed" ]]; then
    say "Installing backend packages…"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -r "$BACKEND/requirements.txt"
    touch "$VENV/.installed"
  fi
}

ensure_node() {
  if [[ ! -d "$FRONTEND/node_modules" ]]; then
    say "Installing website packages…"
    (cd "$FRONTEND" && npm install --silent)
  fi
}

running() { [[ -f "$STATE/$1.pid" ]] && kill -0 "$(cat "$STATE/$1.pid")" 2>/dev/null; }

start_bg() {  # name, dir, command…
  local name="$1" dir="$2"; shift 2
  if running "$name"; then say "$name already running (pid $(cat "$STATE/$name.pid"))"; return; fi
  (cd "$dir" && setsid nohup "$@" </dev/null >"$STATE/$name.log" 2>&1 & echo $! >"$STATE/$name.pid")
}

stop_bg() {
  local name="$1"
  if running "$name"; then
    kill -- "-$(cat "$STATE/$name.pid")" 2>/dev/null || kill "$(cat "$STATE/$name.pid")" 2>/dev/null || true
    say "$name stopped"
  fi
  rm -f "$STATE/$name.pid"
}

seed() {
  (cd "$BACKEND" && ENV_FILE=.env.development "$VENV/bin/python" -m app.scripts.seed_dev)
}

wait_for() {  # url, label
  for _ in $(seq 1 60); do curl -fsS "$1" >/dev/null 2>&1 && { say "$2"; return; }; sleep 1; done
  say "$2 — not answering yet, see: scripts/dev.sh logs"
}

case "${1:-up}" in
  up)
    start_mongo
    ensure_venv
    ensure_node
    if [[ "$(mongosh_eval "db.getSiblingDB('$DEV_DB').users.countDocuments()" 2>/dev/null || echo 0)" == "0" ]]; then
      say "Empty test database — seeding test accounts…"
      seed
    fi
    start_bg backend "$BACKEND" env ENV_FILE=.env.development "$VENV/bin/uvicorn" app.main:app \
      --host 127.0.0.1 --port 8000 --reload --reload-dir app
    start_bg frontend "$FRONTEND" ./node_modules/.bin/vite --host localhost --port 5173 --strictPort
    wait_for http://127.0.0.1:8000/api/health "Backend ✓  http://localhost:8000/api/docs"
    wait_for http://localhost:5173 "Website ✓  http://localhost:5173"
    say "Test logins: customers = phone + OTP 123456 · staff = see backend/app/scripts/seed_dev.py"
    ;;
  down)
    stop_bg frontend
    stop_bg backend
    "${DOCKER[@]}" stop "$MONGO_CONTAINER" >/dev/null 2>&1 && say "Mongo stopped" || true
    ;;
  status)
    for n in backend frontend; do running "$n" && echo "$n: running (pid $(cat "$STATE/$n.pid"))" || echo "$n: stopped"; done
    "${DOCKER[@]}" ps --format '{{.Names}}: {{.Status}}' | grep "$MONGO_CONTAINER" || echo "$MONGO_CONTAINER: stopped"
    ;;
  logs)
    tail -n 50 -f "$STATE/backend.log" "$STATE/frontend.log"
    ;;
  seed)
    start_mongo; ensure_venv; seed
    ;;
  reset)
    start_mongo; ensure_venv
    read -r -p "Wipe the LOCAL test database '$DEV_DB' and seed it fresh? [y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "Cancelled."; exit 0; }
    mongosh_eval "db.getSiblingDB('$DEV_DB').dropDatabase()" >/dev/null
    seed
    ;;
  test)
    start_mongo; ensure_venv
    (cd "$BACKEND" && MONGO_URI="$LOCAL_MONGO_URI" TEST_MONGO_DB_NAME="test_$(date +%s)" "$VENV/bin/python" -m pytest -q -p no:warnings "${@:2}")
    ;;
  *)
    sed -n '2,13p' "$0"; exit 1
    ;;
esac
