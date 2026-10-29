"""Coupon rules — the data a coupon or a bundle carries beyond its benefit.

Shared by regular coupons (`coupons`) and bundles (`coupon_bundles`, which
every code in them inherits), and evaluated in ONE place:
CouponService (`_valid_coupon` for who/when/how often, `check_applicability`
for what is being bought).

Fields (all optional on write; missing = the default shown):

  applies_to               which flows may use it: services (bookings),
                           plans (standard subscription plans), society
                           (society plans), custom (custom plan carts).
  applicable_service_ids   bookings only — the cart must contain one of them
                           (empty = any service)
  applicable_vehicle_types bookings / plans — the car's type must be listed
                           (empty = any)
  applicable_center_ids    bookings only — the service center must be listed
  first_booking_only       the customer's phone/plate has no earlier
                           non-cancelled booking anywhere on the platform
  new_customers_only       stricter: no booking of ANY status, no plan and no
                           society enrolment, by this account or phone
  allowed_customer_phones  only these customers (10-digit numbers)
  allowed_weekdays         bookings — the service day (0 = Monday ... 6)
  time_window_start/_end   bookings — the slot must start inside "HH:MM"
  stackable_with_offers    may sit on top of a first-wash / launch price
                           (default False)
  combine_with_plan        may be used on a visit where a plan covers a car
                           (default False)

A coupon written before these existed has no `applies_to`: it keeps behaving
exactly as it did (services + plans + society, stackable, combinable) until
the startup migration gives it explicit values.
"""
import re

from app.utils.phone import validate_indian_mobile

USES = ("services", "plans", "society", "custom")
# Custom plan carts take a manager discount but no coupon box yet (their
# refund maths splits the discount per car). The use-type exists in the data
# model and the central check; admins can't switch it on until a cart flow
# calls it. Flip when custom_plan_service gains a coupon step.
CUSTOM_COUPONS_ENABLED = False
USE_LABEL = {"services": "Bookings", "plans": "Subscription Plans", "society": "Society Plans", "custom": "Custom Plans"}
LEGACY_USES = ["services", "plans", "society"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

RULE_FIELDS = (
    "applies_to", "applicable_service_ids", "applicable_vehicle_types", "applicable_center_ids", "first_booking_only",
    "new_customers_only", "allowed_customer_phones", "allowed_weekdays", "time_window_start", "time_window_end",
    "stackable_with_offers", "combine_with_plan",
)

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def is_hhmm(value: str | None) -> bool:
    return bool(value and _HHMM.match(value))


def clean_phones(values: list[str] | None) -> list[str]:
    """Distinct 10-digit numbers from whatever formats the admin pasted;
    raises ValueError naming the first one that isn't a mobile number."""
    out: list[str] = []
    for raw in values or []:
        text = str(raw).strip()
        if not text:
            continue
        phone = validate_indian_mobile(text)
        if not phone:
            raise ValueError(f"'{text[:20]}' is not a valid 10-digit mobile number")
        if phone not in out:
            out.append(phone)
    return out


def rules_of(coupon: dict) -> dict:
    """The rules of a coupon dict with defaults (and the legacy reading)."""
    legacy = "applies_to" not in coupon or coupon.get("applies_to") is None
    return {
        "applies_to": list(LEGACY_USES) if legacy else list(coupon.get("applies_to") or []),
        "applicable_service_ids": [str(x) for x in coupon.get("applicable_service_ids") or []],
        "applicable_vehicle_types": [str(x) for x in coupon.get("applicable_vehicle_types") or []],
        "applicable_center_ids": [str(x) for x in coupon.get("applicable_center_ids") or []],
        "first_booking_only": bool(coupon.get("first_booking_only")),
        "new_customers_only": bool(coupon.get("new_customers_only")),
        "allowed_customer_phones": [str(x) for x in coupon.get("allowed_customer_phones") or []],
        "allowed_weekdays": [int(x) for x in coupon.get("allowed_weekdays") or []],
        "time_window_start": coupon.get("time_window_start") or None,
        "time_window_end": coupon.get("time_window_end") or None,
        "stackable_with_offers": True if legacy and "stackable_with_offers" not in coupon else bool(coupon.get("stackable_with_offers")),
        "combine_with_plan": True if legacy and "combine_with_plan" not in coupon else bool(coupon.get("combine_with_plan")),
    }


def describe(coupon: dict, names: dict[str, str] | None = None) -> str:
    """"This Coupon Applies To: Bookings, Subscription Plans · Star Wash ·
    New Customers Only" — the same sentence the admin form shows live.
    `names` maps ids (services, vehicle types, centers) to display names."""
    r = rules_of(coupon)
    names = names or {}

    def nm(i: str) -> str:
        return names.get(i, "Selected")

    parts = [", ".join(USE_LABEL[u] for u in USES if u in r["applies_to"]) or "Nothing (Switched Off Everywhere)"]
    out = [f"This Coupon Applies To: {parts[0]}"]
    if r["applicable_service_ids"]:
        out.append("Services: " + ", ".join(nm(i) for i in r["applicable_service_ids"]))
    if r["applicable_vehicle_types"]:
        out.append("Car Types: " + ", ".join(nm(i) for i in r["applicable_vehicle_types"]))
    if r["applicable_center_ids"]:
        out.append("Centers: " + ", ".join(nm(i) for i in r["applicable_center_ids"]))
    if r["new_customers_only"]:
        out.append("New Customers Only")
    elif r["first_booking_only"]:
        out.append("First Booking Only")
    if r["allowed_customer_phones"]:
        out.append(f"{len(r['allowed_customer_phones'])} Specific Customers")
    if r["allowed_weekdays"]:
        out.append("Days: " + ", ".join(WEEKDAYS[d][:3] for d in sorted(r["allowed_weekdays"])))
    if r["time_window_start"] and r["time_window_end"]:
        out.append(f"Slots Starting {r['time_window_start']}–{r['time_window_end']}")
    out.append("Stacks With First-Wash Offers" if r["stackable_with_offers"] else "Not With First-Wash Offers")
    if r["combine_with_plan"]:
        out.append("Can Be Used On Plan Visits")
    return " · ".join(out)
