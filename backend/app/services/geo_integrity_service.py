"""
Captain GPS integrity — "no one can skip the location check".

A browser/PWA cannot PROVE a real GPS: coordinates come from the phone and a
mock-location app can feed any value. So this is layered server-side
enforcement that makes remote completion need a sustained, physically
plausible fake trail, and leaves evidence when someone tries:

  1. Fix checks (every step)   — present, fresh (device clock vs ours),
                                 accurate enough, not a replay of an earlier
                                 step's fix, inside radius + min(accuracy, cap).
  2. Trail checks (server-held) — the captain_locations breadcrumbs agree
                                 with the claim: no teleports, no frozen
                                 coordinates while "moving", no suspiciously
                                 constant accuracy, enough pings on the way.
  3. Ordering / duration        — steps follow the legal order (status
                                 machine) and a wash can't finish sooner than
                                 a minimum time after it started.

LOCATION NEVER BLOCKS THE CAPTAIN (founder rule, 2026-11-24). A captain
standing at the customer's door must always be able to enter the service
code, start and finish — a weak or missing fix, a pin that is a bit off, a
stale phone clock all used to refuse the step and strand the job. Now every
location reason (no_fix, outside_geofence, poor_accuracy, stale/future/
replayed fix, trail_*) is RECORDED and let through: the step's verdict is
"unverified", a geo_events row + the geo_refusals counter are written, a
fake-looking trail still sets geo_suspect, and the booking service raises a
manager-facing `location_warning` (red, cleared by the manager's Ignore).
The only things that still refuse are non-location rules — `too_quick` (a
time rule), the status order, and the wrong service code itself.
GEOFENCE_ENFORCE=false is the kill switch for that one remaining time rule:
everything is still evaluated and recorded, nothing is refused.
The old approval machinery (geo_overrides) is kept only so old rows and old
clients keep working; nothing creates a requirement for it any more.

Thresholds live in Business Settings (booking_policy geo_* keys + the
existing photo_geofence_radius_m); see docs/ARCHITECTURE.md "Captain GPS
integrity" for the full table and the honest limits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.exceptions import AppException
from app.models.enums import NotificationType
from app.repositories.captain_location_repository import CaptainLocationRepository
from app.utils.geo import haversine_km

logger = logging.getLogger(__name__)

GEO_EVENTS = "geo_events"
GEO_OVERRIDES = "geo_overrides"

STEPS = ("heading", "arrival", "start", "complete")
STEP_LABELS = {"heading": "Heading Out", "arrival": "Arrived", "start": "Start The Service", "complete": "Complete The Job"}
OUTSIDE_MESSAGES = {
    "heading": "You Must Be Able To Share Your Location To Head Out",
    "arrival": "You Must Be At The Customer's Location To Mark Arrived",
    "start": "You Must Be At The Customer's Location To Start The Service",
    "complete": "You Must Be At The Customer's Location To Complete The Job",
}

# Reasons that point at a fake / replayed trail rather than a weak signal.
SUSPECT_CODES = frozenset({"trail_teleport", "trail_frozen", "trail_accuracy_constant", "replayed_fix"})
# The only reasons that still REFUSE a step: time rules, not location rules.
HARD_CODES = frozenset({"too_quick"})


class GeoCheckException(AppException):
    """409: a hard rule (today only `too_quick`, a time rule) refused this
    step. `details` carries the reasons; `can_request_approval` is always
    False now — a location problem never refuses a step."""
    status_code = 409
    error_code = "GEO_CHECK_FAILED"


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reason(code: str, message: str) -> dict:
    return {"code": code, "message": message}


@dataclass
class Fix:
    latitude: float | None
    longitude: float | None
    received_at: datetime
    accuracy_m: float | None = None
    fix_at: datetime | None = None          # the device's claimed time
    speed_mps: float | None = None
    heading_deg: float | None = None
    altitude_m: float | None = None
    secure_context: bool | None = None
    permission_state: str | None = None

    @property
    def present(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def effective_at(self) -> datetime:
        """The time the position is FROM: the device's stamp when it passed
        the clock checks, else when the server received it."""
        return self.fix_at or self.received_at

    @classmethod
    def from_payload(cls, payload: Any, received_at: datetime | None = None) -> "Fix":
        g = lambda name: getattr(payload, name, None)  # noqa: E731
        return cls(
            latitude=g("latitude"), longitude=g("longitude"), received_at=received_at or _now(),
            accuracy_m=g("accuracy_m"), fix_at=_utc(g("fix_at")), speed_mps=g("speed_mps"),
            heading_deg=g("heading_deg"), altitude_m=g("altitude_m"),
            secure_context=g("secure_context"), permission_state=g("permission_state"),
        )

    def breadcrumb_extra(self) -> dict:
        return {
            "fix_at": self.effective_at, "speed_mps": self.speed_mps,
            "heading_deg": self.heading_deg, "altitude_m": self.altitude_m,
        }


@dataclass
class GeoVerdict:
    step: str
    reasons: list[dict] = field(default_factory=list)       # every failed check (only HARD_CODES refuse)
    notes: list[dict] = field(default_factory=list)         # soft signals (record only)
    distance_m: float | None = None
    allowed_m: float | None = None
    pinned: bool = True
    fix: Fix | None = None
    trail_pings: int | None = None

    @property
    def ok(self) -> bool:
        return not self.reasons

    @property
    def suspect_reasons(self) -> list[str]:
        return [r["code"] for r in self.reasons if r["code"] in SUSPECT_CODES]

    @property
    def hard_reasons(self) -> list[dict]:
        """Reasons that still refuse the step (time rules)."""
        return [r for r in self.reasons if r["code"] in HARD_CODES]

    @property
    def location_reasons(self) -> list[dict]:
        """Reasons that are only flagged to the manager (never refuse)."""
        return [r for r in self.reasons if r["code"] not in HARD_CODES]

    def record(self, status: str) -> dict:
        """The per-step evidence stored on the booking (geo_checks.<step>)."""
        fix = self.fix
        data = {
            "status": status,  # verified | unverified | unpinned (| override: old rows)
            "distance_m": self.distance_m,
            "allowed_m": self.allowed_m,
            "accuracy_m": fix.accuracy_m if fix else None,
            "fix_at": fix.fix_at if fix else None,
            "received_at": fix.received_at if fix else _now(),
            "reasons": [r["code"] for r in self.reasons + self.notes],
            "trail_pings": self.trail_pings,
        }
        return data


@dataclass
class Gate:
    verdict: GeoVerdict

    def fields(self, step: str, key_prefix: str = "geo_checks") -> dict:
        """What to $set on the booking with the step's own write."""
        if self.verdict.ok:
            status = "verified" if self.verdict.pinned else "unpinned"
        else:
            status = "unverified"  # failed a location check, let through and flagged
        out: dict[str, Any] = {f"{key_prefix}.{step}": self.verdict.record(status)}
        if self.verdict.fix and self.verdict.fix.present:
            out["geo_last_fix_at"] = self.verdict.fix.effective_at
        if self.verdict.suspect_reasons:
            out["geo_suspect"] = True
        return out


# -- Pure trail maths (unit-tested directly) ---------------------------------

def implied_speed_kmh(a: dict, b: dict) -> tuple[float, float]:
    """(distance_m, km/h) between two trail points {t, lat, lon}."""
    dist_m = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) * 1000
    # Timestamps closer than 10 s say nothing about speed (clock and GPS
    # noise): the floor keeps a 350 m hop between near-simultaneous fixes
    # from reading as 1,000 km/h while 33 km in 10 s still does.
    dt = max(10.0, abs((b["t"] - a["t"]).total_seconds()))
    return dist_m, dist_m / 1000 / (dt / 3600)


def analyze_trail(points: list[dict], policy: dict) -> list[dict]:
    """Suspicious patterns in a time-ordered trail. Each point:
    {t, lat, lon, acc, speed, source}. Returns reasons (may be empty)."""
    reasons: list[dict] = []
    max_kmh = float(policy.get("geo_max_speed_kmh", 150))
    pts = sorted(points, key=lambda p: p["t"])

    # Teleport: two consecutive fixes further apart than anyone can travel.
    # Short hops are ignored — GPS noise at 1 s spacing looks fast.
    for a, b in zip(pts, pts[1:]):
        dist_m, kmh = implied_speed_kmh(a, b)
        noise = max(300.0, (a.get("acc") or 0) + (b.get("acc") or 0))
        if dist_m > noise and kmh > max_kmh:
            reasons.append(_reason("trail_teleport", f"Location Jumped {dist_m / 1000:.1f} Km In {max(1, int(abs((b['t'] - a['t']).total_seconds())))}s"))
            break

    pings = [p for p in pts if p.get("source") == "ping"]
    # Frozen: the exact same coordinates over and over while the device
    # claims to be moving — a mock tool holding one point. Real GPS jitters
    # in the 6th decimal, and a parked phone reports ~0 speed (not flagged).
    need = int(policy.get("geo_frozen_pings", 5))
    run = 1
    for prev, cur in zip(pings, pings[1:]):
        same = round(prev["lat"], 6) == round(cur["lat"], 6) and round(prev["lon"], 6) == round(cur["lon"], 6)
        moving = (prev.get("speed") or 0) >= 0.5 and (cur.get("speed") or 0) >= 0.5
        run = run + 1 if (same and moving) else 1
        if run >= need:
            reasons.append(_reason("trail_frozen", "Identical Location Repeated While Moving"))
            break

    # Constant accuracy across a moving trail — real accuracy varies.
    need_acc = int(policy.get("geo_const_accuracy_pings", 8))
    accs = [p for p in pings if p.get("acc") is not None]
    if len(accs) >= need_acc:
        window = accs[-need_acc:]
        if len({round(p["acc"], 2) for p in window}) == 1:
            span_m = max(haversine_km(window[0]["lat"], window[0]["lon"], p["lat"], p["lon"]) * 1000 for p in window)
            if span_m > 300:
                reasons.append(_reason("trail_accuracy_constant", "GPS Accuracy Never Changes"))
    return reasons


def spaced_count(points: list[dict], spacing_s: float) -> int:
    """Points at least `spacing_s` apart — a burst of pings counts once."""
    count, last = 0, None
    for p in sorted(points, key=lambda x: x["t"]):
        if last is None or (p["t"] - last).total_seconds() >= spacing_s:
            count += 1
            last = p["t"]
    return count


class GeoIntegrityService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.location_repo = CaptainLocationRepository(db)

    # -- evaluation ----------------------------------------------------------

    def _fix_reasons(self, step: str, fix: Fix, policy: dict, last_fix_at: datetime | None) -> list[dict]:
        out: list[dict] = []
        now = fix.received_at
        if fix.fix_at is not None:
            age = (now - fix.fix_at).total_seconds()
            if age > float(policy.get("geo_max_fix_age_s", 120)):
                out.append(_reason("stale_fix", "Your Location Is Out Of Date — Check Your Phone's Date And Time, Then Try Again"))
            elif -age > float(policy.get("geo_future_tolerance_s", 60)):
                out.append(_reason("future_fix", "Your Phone's Clock Looks Wrong — Set Date And Time To Automatic"))
        # Mongo keeps milliseconds: compare at that precision.
        if last_fix_at is not None and fix.effective_at.replace(microsecond=fix.effective_at.microsecond // 1000 * 1000) <= last_fix_at:
            out.append(_reason("replayed_fix", "This Location Was Already Used For An Earlier Step"))
        return out

    async def evaluate(
        self, step: str, booking: dict, captain_id: str, fix: Fix | None, site: tuple[float, float] | None, policy: dict,
    ) -> GeoVerdict:
        v = GeoVerdict(step=step, fix=fix, pinned=site is not None or step == "heading")
        has_fix = bool(fix and fix.present)
        if not has_fix:
            # Nothing to check against an unpinned address (old behaviour: a
            # missing fix is only a manager flag there); with a pin it's a no.
            if site is not None:
                v.reasons.append(_reason("no_fix", "Turn On Location And Allow It For This App, Then Try Again"))
            return v
        assert fix is not None
        last_fix_at = _utc(booking.get("geo_last_fix_at"))
        v.reasons.extend(self._fix_reasons(step, fix, policy, last_fix_at))

        if step != "heading":
            cap = float(policy.get("geo_accuracy_cap_m", 100))
            if fix.accuracy_m is not None and fix.accuracy_m > cap:
                v.reasons.append(_reason("poor_accuracy", f"Your GPS Signal Is Too Weak ({int(fix.accuracy_m)} M) — Move To An Open Area And Try Again"))
            if site is not None:
                radius = float(policy.get("photo_geofence_radius_m", 500))
                slack = min(fix.accuracy_m or 0.0, cap)
                v.allowed_m = round(radius + slack, 1)
                v.distance_m = round(haversine_km(fix.latitude, fix.longitude, site[0], site[1]) * 1000, 1)
                if v.distance_m > v.allowed_m:
                    v.reasons.append(_reason("outside_geofence", OUTSIDE_MESSAGES[step]))

        # Trail + duration checks apply to the on-site steps.
        if step != "heading":
            await self._trail_checks(v, step, booking, captain_id, fix, site, policy)
        if step == "complete":
            floor = float(policy.get("geo_min_service_seconds", settings.GEOFENCE_MIN_SERVICE_SECONDS))
            started = _utc(booking.get("service_started_at"))
            if floor > 0 and started is not None and (fix.received_at - started).total_seconds() < floor:
                v.reasons.append(_reason("too_quick", "The Service Can't Be Completed This Soon After It Started"))
        return v

    async def _trail_checks(self, v: GeoVerdict, step: str, booking: dict, captain_id: str, fix: Fix, site, policy: dict) -> None:
        heading_at = _utc(booking.get("heading_at"))
        if heading_at is None:
            return
        rows = await self.location_repo.list_since(captain_id, heading_at - timedelta(seconds=60))
        points: list[dict] = []
        for r in rows:
            t = _utc(r.get("fix_at")) or _utc(r["at"])
            # Late (offline) pings keep their original time, but one older
            # than the heading-out can't belong to this trip.
            if t is None or t < heading_at - timedelta(seconds=60):
                continue
            points.append({
                "t": t, "lat": r["latitude"], "lon": r["longitude"], "acc": r.get("accuracy_m"),
                "speed": r.get("speed_mps"), "source": r.get("source", "ping"),
            })
        # The step's own fix is the newest point.
        points.append({
            "t": fix.effective_at, "lat": fix.latitude, "lon": fix.longitude, "acc": fix.accuracy_m,
            "speed": fix.speed_mps, "source": "step",
        })
        v.reasons.extend(analyze_trail(points, policy))

        if step == "arrival":
            trail = [p for p in points if p["source"] in ("ping", "heading")]
            v.trail_pings = spaced_count(trail, 10)
            elapsed = (fix.received_at - heading_at).total_seconds()
            need = int(policy.get("geo_min_trail_pings", 2))
            if need > 0 and elapsed > float(policy.get("geo_trail_grace_s", 120)) and v.trail_pings < need:
                v.reasons.append(_reason("trail_missing", "We Can't See Your Trip — Keep The App Open While You Travel"))

    # -- gate (what the booking steps call) ------------------------------------

    async def gate(
        self, step: str, booking: dict, captain_id: str, fix: Fix | None, site: tuple[float, float] | None, policy: dict,
    ) -> Gate:
        """Evaluates and records. A LOCATION problem (outside the boundary, no
        or weak fix, stale/future/replayed fix, a suspicious trail) never
        refuses: the evidence is stored and the step goes through as
        "unverified" — the booking service raises the manager's red warning.
        Only a hard rule (`too_quick`) refuses, and GEOFENCE_ENFORCE=false
        switches even that off."""
        verdict = await self.evaluate(step, booking, captain_id, fix, site, policy)
        if verdict.ok:
            return Gate(verdict)
        hard = verdict.hard_reasons
        if hard and settings.GEOFENCE_ENFORCE:
            await self.record_refusal(booking, step, captain_id, verdict, enforced=True)
            raise GeoCheckException(hard[0]["message"], {
                "step": step, "step_label": STEP_LABELS[step], "reasons": verdict.reasons,
                "distance_m": verdict.distance_m, "allowed_m": verdict.allowed_m,
                "can_request_approval": False, "approval": {"status": "none"},
            })
        await self.record_refusal(booking, step, captain_id, verdict, enforced=False)
        return Gate(verdict)

    # -- evidence ----------------------------------------------------------------

    async def record_refusal(
        self, booking: dict, step: str, captain_id: str, verdict: GeoVerdict, *, enforced: bool, overridden: bool = False,
    ) -> None:
        """Every failed check leaves a row, a counter on the booking and —
        for a fake-looking trail — the geo_suspect flag + one manager alert."""
        booking_id = str(booking["_id"])
        fix = verdict.fix
        try:
            await self.db[GEO_EVENTS].insert_one({
                "booking_id": booking_id, "captain_id": captain_id, "step": step,
                "reasons": [r["code"] for r in verdict.reasons], "distance_m": verdict.distance_m, "allowed_m": verdict.allowed_m,
                "latitude": fix.latitude if fix else None, "longitude": fix.longitude if fix else None,
                "accuracy_m": fix.accuracy_m if fix else None, "fix_at": fix.fix_at if fix else None,
                "enforced": enforced, "overridden": overridden, "at": _now(),
            })
            update: dict[str, Any] = {"$inc": {"geo_refusals": 1}}
            suspects = verdict.suspect_reasons
            if suspects:
                update["$addToSet"] = {"geo_suspect_reasons": {"$each": suspects}}
                update["$set"] = {"geo_suspect": True}
            first_suspect = False
            if suspects:
                first = await self.db.bookings.update_one({"_id": booking["_id"], "geo_suspect": {"$ne": True}}, update)
                first_suspect = first.modified_count == 1
                if not first_suspect:
                    await self.db.bookings.update_one({"_id": booking["_id"]}, update)
            else:
                await self.db.bookings.update_one({"_id": booking["_id"]}, update)
            if first_suspect:
                await self._notify_manager(
                    booking, f"🚨 Suspicious Location — Booking {booking.get('booking_number')}",
                    "The captain's location trail looks faked (" + ", ".join(suspects).replace("_", " ") + "). Worth a look.",
                )
        except Exception:  # noqa: BLE001 — evidence must never break the step's own answer
            logger.exception("geo evidence write failed for booking %s", booking_id)

    async def _notify_manager(self, booking: dict, title: str, text: str) -> None:
        from app.services.notification_service import NotificationService

        center = await self.db.service_centers.find_one({"_id": ObjectId(booking["service_center_id"])}) if ObjectId.is_valid(booking.get("service_center_id") or "") else None
        if center and center.get("manager_id"):
            await NotificationService(self.db).notify(
                center["manager_id"], title, text, NotificationType.BOOKING, str(booking["_id"]), send_whatsapp=False, background=True,
            )

    async def _policy(self) -> dict:
        from app.services.booking_policy_service import BookingPolicyService

        return await BookingPolicyService(self.db).get_policy()

    async def summary(self, booking: dict) -> dict:
        """Manager/admin/captain view of a job's location verdict."""
        rows = await self.db[GEO_OVERRIDES].find({"booking_id": str(booking["_id"])}).sort("requested_at", -1).to_list(length=20)
        return {
            "checks": booking.get("geo_checks") or {},
            "suspect": bool(booking.get("geo_suspect")),
            "suspect_reasons": booking.get("geo_suspect_reasons") or [],
            "override": bool(booking.get("geo_override")),
            "refusals": int(booking.get("geo_refusals") or 0),
            "approvals": [
                {
                    "id": str(r["_id"]), "step": r["step"], "status": r["status"], "requested_at": r.get("requested_at"),
                    "reason": r.get("reason"), "decided_by_name": r.get("decided_by_name"), "decided_at": r.get("decided_at"),
                    "expires_at": r.get("expires_at"),
                }
                for r in rows
            ],
        }

    async def ensure_indexes(self) -> None:
        await self.db[GEO_EVENTS].create_index("at", expireAfterSeconds=90 * 24 * 3600)
        await self.db[GEO_EVENTS].create_index([("booking_id", 1), ("at", -1)])
        await self.db[GEO_OVERRIDES].create_index([("booking_id", 1), ("step", 1), ("requested_at", -1)])
        await self.db[GEO_OVERRIDES].create_index("requested_at", expireAfterSeconds=180 * 24 * 3600)
