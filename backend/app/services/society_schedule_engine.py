"""
Society premium-wash scheduling — the pure math (no database). See
docs/SOCIETY_PLANS.md §9 "Scheduling".

  * pattern_dates     — which dates a repeat rule lands on
  * rotation_patterns — spread N societies over the chosen weekend days
  * lay_out           — who washes which car when, on one visit day
  * project           — walk a society's upcoming visits in date order and
                        decide which cars each one covers, never spending
                        more premium washes than a car has left

Everything here takes and returns plain data so it can be tested without
Mongo and reused by the planner preview and the booking sweep alike.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}


# ---------------------------------------------------------------------------
# Repeat patterns
# ---------------------------------------------------------------------------


def pattern_dates(pattern: dict, start: date, end: date, *, rule_start: date | None = None, rule_end: date | None = None) -> list[date]:
    """Dates in [start, end] (both inclusive) a pattern lands on.

    kinds:
      weekly         — every <weekday>
      monthly_nth    — the 1st/2nd/…/5th <weekday> of each calendar month
      every_n_weeks  — every <interval_weeks> weeks from <anchor_date>
    """
    lo = max(start, rule_start) if rule_start else start
    hi = min(end, rule_end) if rule_end else end
    if hi < lo:
        return []
    weekday = int(pattern["weekday"])
    day = lo + timedelta(days=(weekday - lo.weekday()) % 7)
    kind = pattern.get("kind") or "weekly"
    out: list[date] = []
    if kind == "every_n_weeks":
        anchor = date.fromisoformat(pattern["anchor_date"])
        interval = max(1, int(pattern.get("interval_weeks") or 1))
        while day <= hi:
            delta = (day - anchor).days
            if delta >= 0 and (delta // 7) % interval == 0:
                out.append(day)
            day += timedelta(days=7)
    elif kind == "monthly_nth":
        weeks = {int(w) for w in pattern.get("weeks") or []}
        while day <= hi:
            if (day.day - 1) // 7 + 1 in weeks:
                out.append(day)
            day += timedelta(days=7)
    else:
        while day <= hi:
            out.append(day)
            day += timedelta(days=7)
    return out


def pattern_label(pattern: dict) -> str:
    name = WEEKDAY_NAMES[int(pattern["weekday"])]
    kind = pattern.get("kind") or "weekly"
    if kind == "monthly_nth":
        weeks = sorted(int(w) for w in pattern.get("weeks") or [])
        return f"{' & '.join(ORDINALS.get(w, str(w)) for w in weeks)} {name} of the month"
    if kind == "every_n_weeks":
        n = int(pattern.get("interval_weeks") or 1)
        anchor = date.fromisoformat(pattern["anchor_date"])
        every = f"Every {name}" if n == 1 else f"Every {n} weeks on {name}"
        return f"{every} (from {anchor.day} {anchor.strftime('%b')})"
    return f"Every {name}"


def rotation_patterns(count: int, weekdays: list[int], start: date) -> list[dict]:
    """Round-robin `count` societies over the chosen days of the week, in
    order: the first society gets the first chosen day on/after `start`,
    the second the next chosen day, … wrapping into the following weeks.
    The cycle repeats every ceil(count / len(weekdays)) weeks, so every
    society is visited exactly once per round — 4 societies over Sat+Sun:
    A Sat wk1, B Sun wk1, C Sat wk2, D Sun wk2, then A again on Sat wk3."""
    days = sorted({int(d) for d in weekdays})
    if not days or count <= 0:
        return []
    firsts = sorted(start + timedelta(days=(d - start.weekday()) % 7) for d in days)
    period = math.ceil(count / len(firsts))
    out = []
    for i in range(count):
        first = firsts[i % len(firsts)] + timedelta(weeks=i // len(firsts))
        out.append({"kind": "every_n_weeks", "weekday": first.weekday(), "interval_weeks": period, "anchor_date": first.isoformat()})
    return out


# ---------------------------------------------------------------------------
# One visit day's layout
# ---------------------------------------------------------------------------


@dataclass
class Slot:
    key: str
    start: int  # minutes from midnight (IST)
    end: int


def hhmm_to_min(hhmm: str) -> int:
    h, m = [int(p) for p in hhmm.split(":")]
    return h * 60 + m


def min_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def slot_from_key(key: str) -> Slot:
    a, b = key.split("-", 1)
    return Slot(key, hhmm_to_min(a.strip()), hhmm_to_min(b.strip()))


def slot_containing(minute: int, slots: list[Slot]) -> Slot | None:
    return next((s for s in slots if s.start <= minute < s.end), None)


def _next_free(start: int, length: int, busy: list[tuple[int, int]], buffer: int) -> int:
    """Earliest t >= start with [t, t+length) clear of every busy window
    by at least `buffer` minutes (the captain's travel gap)."""
    t = start
    moved = True
    while moved:
        moved = False
        for b_start, b_end in busy:
            if t < b_end + buffer and b_start < t + length + buffer:
                t = b_end + buffer
                moved = True
    return t


def _fit(t: int, hi: int, unit: dict, busy: list[tuple[int, int]], buffer: int, pin: Slot | None, window: list[Slot]) -> tuple[int, Slot] | None:
    """Earliest start >= t (and < hi) for this unit in this lane, plus the
    booking slot it falls in — one of the visit's own slots (or the
    resident's pinned slot). Every car of a multi-car unit must START inside
    that one booking slot (they share one booking visit), so a unit that
    would spill its last car past the slot's end moves to the next slot."""
    last_offset = int(unit.get("last_offset") or 0)
    candidates = [pin] if pin else sorted(window, key=lambda s: s.start)
    while True:
        t = _next_free(t, unit["minutes"], busy, buffer)
        if t >= hi:
            return None
        slot = slot_containing(t, candidates)
        if slot is None:
            nxt = min((s.start for s in candidates if s.start > t), default=None)
            if nxt is None:
                return None
            t = nxt
            continue
        if t + last_offset <= slot.end:
            return t, slot
        if pin:
            return None
        t = slot.end


def lay_out(
    units: list[dict], lanes: list[str | None], per_lane: int, window: list[Slot], all_slots: list[Slot], buffer: int,
    busy: dict[str | None, list[tuple[int, int]]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Give each unit (one resident's cars, washed back to back) a lane
    (captain), a start minute and the booking slot that start falls in.

    unit: {"cars": n, "minutes": total wash time, "pin": Slot | None, ...}
    A captain takes at most `per_lane` cars that day; each unit starts
    after his previous one plus the travel buffer, and never inside one of
    his other jobs (`busy`). A pinned unit (a resident's own slot) must start
    inside its slot; others anywhere inside the visit window. What doesn't
    fit comes back in the second list."""
    if not window:
        return [], list(units)
    w_start = min(s.start for s in window)
    w_end = max(s.end for s in window)
    state = [{"free": w_start, "count": 0, "busy": list((busy or {}).get(lane) or [])} for lane in lanes]
    placed, overflow = [], []
    for unit in units:
        pin: Slot | None = unit.get("pin")
        lo, hi = (pin.start, pin.end) if pin else (w_start, w_end)
        best = None
        for index, lane in enumerate(state):
            if lane["count"] + unit["cars"] > per_lane:
                continue
            found = _fit(max(lane["free"], lo), hi, unit, lane["busy"], buffer, pin, window)
            if found and (best is None or found[0] < best[0]):
                best = (found[0], index, found[1])
        if best is None:
            overflow.append(unit)
            continue
        start, index, slot = best
        lane = state[index]
        lane["free"] = start + unit["minutes"] + buffer
        lane["count"] += unit["cars"]
        lane["busy"].append((start, start + unit["minutes"]))
        placed.append({**unit, "lane": index, "captain_id": lanes[index], "start": start, "slot_key": slot.key})
    return placed, overflow


# ---------------------------------------------------------------------------
# Projection over a society's upcoming visits
# ---------------------------------------------------------------------------


@dataclass
class Car:
    sub_id: str
    vehicle_id: str
    enrollment_id: str
    customer_id: str
    remaining: int
    cycle_start: date
    end: date  # exclusive: a wash must be BEFORE this date
    total: int = 1
    minutes: int = 45
    flat: str = ""
    plate: str = ""
    washed: set[date] = field(default_factory=set)  # booked/done premium washes


@dataclass
class Visit:
    id: str
    kind: str  # "society" | "resident"
    date: date
    status: str
    slot_keys: list[str] = field(default_factory=list)
    captain_ids: list[str] = field(default_factory=list)
    per_captain: int = 6
    sub_ids: list[str] = field(default_factory=list)  # resident visits: the cars
    excluded: set[str] = field(default_factory=set)
    order: int = 0


PLANNED = "planned"


def ideal_gap(car: Car) -> int:
    """Spacing between a car's premium washes: its plan month spread over
    its quota, minus a couple of days of slack."""
    cycle = max(1, (car.end - car.cycle_start).days)
    return max(3, cycle // max(1, car.total) - 2)


def project(
    cars: list[Car], visits: list[Visit], slots: dict[str, Slot], buffer: int,
    busy: dict[date, dict[str | None, list[tuple[int, int]]]] | None = None,
) -> dict[str, dict]:
    """Decide which cars each PLANNED visit covers, walking visits in date
    order (a resident's own repeat wash before the society visit day on the
    same date). Already generated / skipped visits are left as they are —
    their bookings already spent (or never touched) the quota.

    Returns {visit_id: {"placed": [unit…], "overflow": [unit…], "skipped": [{sub_id, reason}]}}
    where a unit is {"enrollment_id", "sub_ids", "cars", "minutes", "captain_id", "slot_key", "start"}.

    Never spends more than a car's remaining premium washes, never washes a
    car twice on one date, never books after its plan month ends."""
    by_sub = {c.sub_id: c for c in cars}
    budget = {c.sub_id: int(c.remaining) for c in cars}
    washed = {c.sub_id: set(c.washed) for c in cars}
    # Captains' booked time per date — updated as units are placed, so a
    # caller sharing one dict across societies sees each captain's whole day.
    busy = busy if busy is not None else {}
    ordered = sorted(visits, key=lambda v: (v.date, 0 if v.kind == "resident" else 1, v.order))
    out: dict[str, dict] = {}

    # Later chances per car (for urgency): planned visits that could still
    # cover it after a given date.
    chance_dates: dict[str, list[date]] = {c.sub_id: [] for c in cars}
    society_days = sorted(v.date for v in ordered if v.kind == "society" and v.status == PLANNED)
    for c in cars:
        dates = [d for d in society_days if c.cycle_start <= d < c.end]
        for v in ordered:
            if v.kind == "resident" and v.status == PLANNED and c.sub_id in v.sub_ids and c.cycle_start <= v.date < c.end:
                dates.append(v.date)
        chance_dates[c.sub_id] = sorted(dates)

    def later_chances(sub_id: str, on: date) -> int:
        dates = chance_dates.get(sub_id) or []
        return len(dates) - bisect_right(dates, on)

    # Enrollments with their own repeat wash (or any booking) on a date sit
    # that society day out — two bookings for one resident at once would
    # clash in the booking flow.
    resident_dates: dict[str, set[date]] = {}
    for v in ordered:
        if v.kind == "resident" and v.status not in ("skipped", "cancelled"):
            for sid in v.sub_ids:
                car = by_sub.get(sid)
                if car:
                    resident_dates.setdefault(car.enrollment_id, set()).add(v.date)
    for c in cars:
        for d in c.washed:
            resident_dates.setdefault(c.enrollment_id, set()).add(d)

    for v in ordered:
        result = {"placed": [], "overflow": [], "skipped": []}
        out[v.id] = result
        if v.status != PLANNED:
            continue
        day_busy = busy.setdefault(v.date, {})
        if v.kind == "resident":
            chosen = []
            for sid in v.sub_ids:
                car = by_sub.get(sid)
                reason = _cannot(car, sid, v, budget, washed)
                if reason:
                    result["skipped"].append({"sub_id": sid, "reason": reason})
                    continue
                chosen.append(car)
            if not chosen:
                continue
            slot = slots.get(v.slot_keys[0]) if v.slot_keys else None
            unit = _unit(chosen, pin=slot)
            lanes = [v.captain_ids[0] if v.captain_ids else None]
            placed, overflow = lay_out([unit], lanes, 99, [slot] if slot else [], list(slots.values()), buffer, day_busy)
            if overflow:
                # The resident's own slot stands even when the captain is
                # booked solid — the booking goes to the queue unassigned.
                placed = [{**unit, "lane": 0, "captain_id": None, "start": slot.start if slot else None,
                           "slot_key": slot.key if slot else None, "warning": "Captain is busy in this slot — assign one from the queue"}]
            for p in placed:
                _spend(p, budget, washed, v.date, day_busy)
            result["placed"] = placed
            continue

        # Society visit day: the most urgent due cars, grouped per resident.
        due: list[tuple[tuple, Car]] = []
        for car in cars:
            if car.sub_id in v.excluded:
                result["skipped"].append({"sub_id": car.sub_id, "reason": "Taken off this day"})
                continue
            if v.date in resident_dates.get(car.enrollment_id, set()):
                # Their own repeat wash, or another booking, that day.
                result["skipped"].append({"sub_id": car.sub_id, "reason": "Has another wash booked that day"})
                continue
            reason = _cannot(car, car.sub_id, v, budget, washed)
            if reason:
                result["skipped"].append({"sub_id": car.sub_id, "reason": reason})
                continue
            surplus = budget[car.sub_id] - later_chances(car.sub_id, v.date)
            last = max((d for d in washed[car.sub_id] if car.cycle_start <= d < v.date), default=None)
            if surplus < 1 and last is not None and (v.date - last).days < ideal_gap(car):
                result["skipped"].append({"sub_id": car.sub_id, "reason": f"Spaced out — last premium wash {last.isoformat()}"})
                continue
            due.append(((-surplus, car.end, car.flat.lower(), car.plate), car))
        groups: dict[str, list[tuple[tuple, Car]]] = {}
        for key, car in due:
            groups.setdefault(car.enrollment_id, []).append((key, car))
        units = []
        for members in groups.values():
            members.sort(key=lambda kc: kc[0])
            units.append((members[0][0], _unit([c for _, c in members])))
        units.sort(key=lambda ku: ku[0])
        lanes = list(v.captain_ids) or [None]
        capacity = len(lanes) * v.per_captain
        picked, over_capacity, total = [], [], 0
        for _key, unit in units:
            if total + unit["cars"] <= capacity:
                picked.append(unit)
                total += unit["cars"]
            else:
                over_capacity.append({**unit, "reason": "Visit day is full"})
        window = [slots[k] for k in v.slot_keys if k in slots]
        picked.sort(key=lambda u: (u["flat"].lower(), u["sub_ids"][0]))
        placed, no_time = lay_out(picked, lanes, v.per_captain, window, list(slots.values()), buffer, day_busy)
        for p in placed:
            _spend(p, budget, washed, v.date, day_busy)
        result["placed"] = placed
        result["overflow"] = over_capacity + [{**u, "reason": "No time left in the visit window"} for u in no_time]
    return out


def _cannot(car: Car | None, sub_id: str, v: Visit, budget: dict[str, int], washed: dict[str, set[date]]) -> str | None:
    if car is None:
        return "Not on a live plan"
    if sub_id in v.excluded:
        return "Taken off this day"
    if v.date >= car.end:
        return f"Plan month ends {car.end.isoformat()}"
    if v.date < car.cycle_start:
        return "Before this plan month starts"
    if v.date in washed[sub_id]:
        return "Already booked that day"
    if budget[sub_id] < 1:
        return "No premium washes left this month"
    return None


def _unit(cars: list[Car], pin: Slot | None = None) -> dict:
    return {
        "enrollment_id": cars[0].enrollment_id,
        "customer_id": cars[0].customer_id,
        "sub_ids": [c.sub_id for c in cars],
        "cars": len(cars),
        "minutes": sum(max(1, c.minutes) for c in cars),
        # The latest a car of this unit can start after the first one (the
        # booking flow orders a visit's cars its own way — assume the worst).
        "last_offset": sum(max(1, c.minutes) for c in cars) - min(max(1, c.minutes) for c in cars),
        "flat": cars[0].flat or "",
        "pin": pin,
    }


def _spend(unit: dict, budget: dict[str, int], washed: dict[str, set[date]], on: date, day_busy: dict) -> None:
    for sid in unit["sub_ids"]:
        budget[sid] -= 1
        washed[sid].add(on)
    if unit.get("captain_id") is not None and unit.get("start") is not None:
        day_busy.setdefault(unit["captain_id"], []).append((unit["start"], unit["start"] + unit["minutes"]))
