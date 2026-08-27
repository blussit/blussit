"""
HTTP + WebSocket smoke test against a REAL running dev server (not the
service layer directly, unlike backend/tests/) — proves the whole stack
wired together correctly: real HTTP requests through the real auth
middleware, and the WebSocket broadcast plumbing over a real network
socket, not just direct function calls.

Requires the backend already running (e.g. `uvicorn app.main:app --port
8010` from backend/) and reachable at BASE/WS_BASE below — this script
does not start it. Point it at whatever database that server is
connected to; every piece of data this script creates is deleted again
at the end (self-cleaning, matching the pytest suite's own discipline),
so it's safe to run repeatedly against a real dev database.

Run with:  python -m app.scripts.smoke_test_http_ws
"""
import asyncio
import json
import random
import sys
from datetime import datetime, timedelta, timezone

import httpx
import websockets
from bson import ObjectId

BASE = "http://127.0.0.1:8010/api/v1"
WS_BASE = "ws://127.0.0.1:8010/api/v1"


def ok(label):
    print(f"PASS: {label}")


def fail(label, detail=""):
    print(f"FAIL: {label} {detail}")
    sys.exit(1)


async def cleanup(customer_id: str | None, booking_id: str | None):
    """Best-effort — runs even if an earlier assertion already called
    sys.exit, since main() wraps its body in try/finally around this."""
    from app.core.database import close_mongo_connection, connect_to_mongo, mongodb

    await connect_to_mongo()
    db = mongodb.db
    if customer_id:
        await db.bookings.delete_many({"customer_id": customer_id})
        await db.vehicles.delete_many({"owner_id": customer_id})
        await db.addresses.delete_many({"owner_id": customer_id})
        await db.notifications.delete_many({"user_id": customer_id})
        await db.users.delete_one({"_id": ObjectId(customer_id)})
    await close_mongo_connection()


async def main():
    state = {"customer_id": None, "booking_id": None}
    try:
        await run(state)
    finally:
        await cleanup(state["customer_id"], state["booking_id"])


async def run(state: dict):
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as client:
        # --- 1. Register a real customer over HTTP ---
        n = random.randint(100000, 999999)
        register_payload = {"full_name": "Smoke Test Customer", "email": f"smoketest{n}@example.com", "phone": f"9{n:09d}"[:10], "password": "Test@12345"}
        r = await client.post("/auth/register", json=register_payload)
        if r.status_code != 200:
            fail("register", r.text)
        data = r.json()["data"]
        access_token = data["access_token"]
        customer_id = data["user"]["id"]
        state["customer_id"] = customer_id
        ok(f"register -> customer {customer_id}")

        client.headers["Authorization"] = f"Bearer {access_token}"

        # --- 2. /auth/me ---
        r = await client.get("/auth/me")
        if r.status_code != 200 or r.json()["data"]["id"] != customer_id:
            fail("auth/me", r.text)
        ok("auth/me")

        # --- 3. Find a real vehicle type + service + service center from seed data ---
        r = await client.get("/vehicle-types")
        vehicle_types = r.json()["data"]
        hatchback = next((v for v in vehicle_types if v["slug"] == "hatchback"), vehicle_types[0])
        ok(f"vehicle-types -> {len(vehicle_types)} types")

        r = await client.get("/services", params={"page": 1, "page_size": 5})
        services = r.json()["data"]
        if not services:
            fail("services", "no services found")
        service_id = services[0]["id"]
        ok(f"services -> {len(services)} services")

        centers = []
        for pincode in ("452001", "452010", "452099", "452098"):
            r = await client.get("/service-centers/lookup", params={"pincode": pincode})
            centers = r.json().get("data") or []
            if centers:
                break
        if not centers:
            fail("service-centers/lookup", "no center found for any test pincode — check seed data")
        center = centers[0]
        center_id = center["id"]
        ok(f"service-centers/lookup -> center {center_id}")

        # --- 4. Add a vehicle + address over HTTP ---
        r = await client.post("/vehicles", json={"vehicle_type": hatchback["id"], "brand": "Maruti", "model": "Swift", "registration_number": f"MP09SMK{n % 10000:04d}"})
        if r.status_code != 200:
            fail("create vehicle", r.text)
        vehicle_id = r.json()["data"]["id"]
        ok(f"create vehicle -> {vehicle_id}")

        r = await client.post("/addresses", json={"label": "Smoke Test", "line1": "Test Street", "city": "Indore", "state": "MP", "pincode": center["location"]["pincode"], "latitude": center["location"]["latitude"], "longitude": center["location"]["longitude"]})
        if r.status_code != 200:
            fail("create address", r.text)
        address_id = r.json()["data"]["id"]
        ok(f"create address -> {address_id}")

        # --- 5. Slots endpoint never leaks raw capacity, over real HTTP ---
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        r = await client.get(f"/service-centers/{center_id}/slots", params={"date": tomorrow})
        if r.status_code != 200:
            fail("slots", r.text)
        slots = r.json()["data"]
        if not slots:
            fail("slots", "no slots generated for this center")
        for s in slots:
            if "capacity" in s or "booked_count" in s:
                fail("slots leaks raw capacity", json.dumps(s))
        ok(f"slots -> {len(slots)} slots, no capacity leak")
        open_slot = next((s for s in slots if s["status"] != "full"), None)
        if not open_slot:
            fail("slots", "no bookable slot available tomorrow")

        # --- 6. Open a WebSocket connection FIRST, subscribed to what we're about to change ---
        ws_url = f"{WS_BASE}/ws?token={access_token}"
        async with websockets.connect(ws_url) as ws:
            hello = json.loads(await ws.recv())
            if hello.get("type") != "connected":
                fail("ws connect", hello)
            ok("ws connected")

            await ws.send(json.dumps({"action": "subscribe", "channel": f"slots:{center_id}:{tomorrow}"}))
            ack = json.loads(await ws.recv())
            if ack.get("type") != "subscribed":
                fail("ws subscribe slots channel", ack)
            ok("ws subscribed to slots channel")

            # --- 7. Create a real booking over HTTP while the socket listens ---
            r = await client.post("/bookings", json={"vehicle_id": vehicle_id, "address_id": address_id, "service_ids": [service_id], "scheduled_date": tomorrow, "scheduled_slot": open_slot["key"]})
            if r.status_code != 200:
                fail("create booking", r.text)
            booking = r.json()["data"]
            booking_id = booking["id"]
            state["booking_id"] = booking_id
            ok(f"create booking over HTTP -> {booking_id}")

            # The slots-changed broadcast should arrive on the already-open socket.
            try:
                push = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            except asyncio.TimeoutError:
                fail("ws push after booking create", "no message received within 5s")
            if push.get("type") != "changed" or push.get("channel") != f"slots:{center_id}:{tomorrow}":
                fail("ws push after booking create", push)
            ok("real-time WS push received after booking creation (slots channel)")

            # --- 8. Subscribe to this specific booking's channel too, then cancel it ---
            await ws.send(json.dumps({"action": "subscribe", "channel": f"booking:{booking_id}"}))
            ack = json.loads(await ws.recv())
            if ack.get("type") != "subscribed":
                fail("ws subscribe booking channel", ack)

            r = await client.get(f"/bookings/{booking_id}")
            if r.status_code != 200:
                fail("get booking", r.text)
            ok("get booking")

            # Negative-path security check over REAL HTTP + auth middleware —
            # a customer must not be able to set priority.
            r = await client.patch(f"/bookings/{booking_id}/priority", json={"priority": "high"})
            if r.status_code not in (401, 403, 404):
                fail("customer priority update should be forbidden", f"got {r.status_code}: {r.text}")
            ok(f"customer forbidden from setting priority (HTTP {r.status_code})")

            r = await client.post(f"/bookings/{booking_id}/cancel", json={"reason": "smoke test cleanup"})
            if r.status_code != 200:
                fail("cancel booking", r.text)
            ok("cancel booking over HTTP")

            try:
                push = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            except asyncio.TimeoutError:
                fail("ws push after cancel", "no message received within 5s")
            if push.get("type") != "changed" or push.get("channel") != f"booking:{booking_id}":
                fail("ws push after cancel", push)
            ok("real-time WS push received after cancellation (booking channel)")

        print("\nALL HTTP+WS SMOKE ASSERTIONS PASSED")


asyncio.run(main())
