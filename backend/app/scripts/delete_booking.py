"""
Permanently deletes bookings (and the rows that only exist because of them)
from the database in the active env file (backend/.env.development by default;
ENV_FILE=.env.production for the live one). Built for cleaning out test / mistaken
entries.

  python -m app.scripts.delete_booking BK0001-BK0007 BK0009            # look only
  python -m app.scripts.delete_booking BK0001-BK0007 BK0009 --delete   # really delete
  python -m app.scripts.delete_booking BK0015 --phone 98XXXXXXXX       # one booking, phone must match

Numbers can be single (BK0009) or ranges (BK0001-BK0007). Run it from backend/.

Safe by default:
  * WITHOUT --delete it only SHOWS every booking it found and what it would do.
    Read that list against what you expect before running with --delete.
  * --phone (optional) requires every listed booking to belong to that number.
  * A number that doesn't exist is reported and skipped; nothing else is guessed.
  * A multi-car visit is only deleted when EVERY car of it is in your list.
  * It refuses (touches nothing) if a booking has captain wallet money, a PAID
    online payment or a complaint attached — those need a human decision, not a
    delete. The dry run tells you which.
  * Before deleting it writes everything it is about to remove to one JSON
    backup file in the folder you ran it from, so it can be restored by hand.

Removed together with a booking: status history, in-app notifications, review,
GPS breadcrumbs, unpaid payment links, the thank-you-page ticket. Handed back:
a coupon's usage, a subscription pass's spent value, late-cancellation charges
it carried (back on the customer's account), and the slot seat — only from the
booking that RECORDS holding it (holds_seat), through the same release helper
the app uses, in the same transaction as the delete (once per visit). Kept on
purpose: audit-log entries and WhatsApp send records; customer accounts are
never touched.
"""
import argparse
import asyncio
import re
from datetime import datetime
from pathlib import Path

from bson import json_util

from app.core.config import settings
from app.core.database import close_mongo_connection, connect_to_mongo, mongodb


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")[-10:]


def expand(tokens: list[str]) -> list[str]:
    """['BK0001-BK0003', 'bk9'] -> ['BK0001', 'BK0002', 'BK0003', 'BK9']"""
    numbers: list[str] = []
    for token in tokens:
        token = token.strip().upper()
        match = re.fullmatch(r"([A-Z]+)(\d+)-(?:[A-Z]+)?(\d+)", token)
        if match:
            prefix, first, last = match.group(1), match.group(2), match.group(3)
            width = len(first)
            if int(last) < int(first) or int(last) - int(first) > 500:
                raise SystemExit(f"Bad range {token}")
            numbers += [f"{prefix}{n:0{width}d}" for n in range(int(first), int(last) + 1)]
        else:
            numbers.append(token)
    return list(dict.fromkeys(numbers))


def _row(b: dict) -> str:
    date = b.get("scheduled_date")
    date = date.strftime("%d %b %Y") if hasattr(date, "strftime") else date
    return (
        f"  {b.get('booking_number'):8s} {str(b.get('status')):18s} {str(b.get('customer_phone')):12s} "
        f"Rs {b.get('total_amount')!s:8s} {str(date):12s} {str(b.get('scheduled_slot')):12s} "
        f"{b.get('payment_method')}/{b.get('payment_status')}  "
        f"{'by-manager ' if b.get('completed_by_role') == 'manager' else ''}"
        f"{'visit:' + str(b['booking_group_id'])[-6:] if b.get('booking_group_id') else ''}"
    )


async def run(tokens: list[str], phone: str | None, do_delete: bool) -> int:
    await connect_to_mongo(build_indexes=False)
    db = mongodb.db
    print(f"Database: {settings.MONGO_DB_NAME}\n")

    wanted = expand(tokens)
    bookings = await db.bookings.find({"booking_number": {"$in": wanted}}).sort("booking_number", 1).to_list(length=1000)
    found_numbers = {b["booking_number"] for b in bookings}
    missing = [n for n in wanted if n not in found_numbers]
    dupes = [n for n in found_numbers if sum(1 for b in bookings if b["booking_number"] == n) > 1]

    print(f"Asked for {len(wanted)} booking number(s); found {len(bookings)}.")
    if missing:
        print("  Not found (skipped): " + ", ".join(missing))
    problems: list[str] = []
    if dupes:
        problems.append("More than one booking has the number(s): " + ", ".join(dupes))
    if not bookings:
        print("Nothing to do.")
        await close_mongo_connection()
        return 1
    print("\n  number   status             phone        amount       date         slot         payment\n" + "\n".join(_row(b) for b in bookings))

    ids = [str(b["_id"]) for b in bookings]
    id_set = set(ids)

    if phone:
        wrong = [b["booking_number"] for b in bookings if _digits(b.get("customer_phone") or "") != _digits(phone)]
        if wrong:
            problems.append(f"Not for phone {phone}: " + ", ".join(wrong))

    # A visit is deleted whole or not at all.
    for group_id in {b["booking_group_id"] for b in bookings if b.get("booking_group_id")}:
        siblings = await db.bookings.find({"booking_group_id": group_id}).to_list(length=50)
        outside = [s["booking_number"] for s in siblings if str(s["_id"]) not in id_set]
        if outside:
            problems.append(f"A visit also includes {', '.join(outside)} which is not in your list — delete the whole visit or none of it")

    # Attached rows that need a human decision.
    wallet = await db.wallet_transactions.find({"booking_id": {"$in": ids}}).to_list(length=500)
    if wallet:
        problems.append(f"{len(wallet)} captain wallet transaction(s) reference these bookings (captain money) — settle those first")
    paid_orders = await db.payment_orders.find(
        {"$or": [{"booking_id": {"$in": ids}}, {"booking_ids": {"$in": ids}}], "status": {"$in": ["paid", "paid_attention"]}}
    ).to_list(length=100)
    if paid_orders:
        problems.append(f"{len(paid_orders)} PAID online payment(s) are attached — a refund is a Razorpay matter, not a delete")
    complaints = await db.complaints.find({"booking_id": {"$in": ids}}).to_list(length=100)
    if complaints:
        problems.append(f"{len(complaints)} complaint(s) are attached")

    def child_filters(booking_ids: list[str]) -> dict[str, dict]:
        return {
            "booking_status_history": {"booking_id": {"$in": booking_ids}},
            "notifications": {"reference_id": {"$in": booking_ids}},
            "reviews": {"booking_id": {"$in": booking_ids}},
            "captain_locations": {"booking_id": {"$in": booking_ids}},
            "purchase_confirmations": {"reference_id": {"$in": booking_ids}},
            "payment_orders": {
                "$or": [{"booking_id": {"$in": booking_ids}}, {"booking_ids": {"$in": booking_ids}}],
                "status": {"$nin": ["paid", "paid_attention"]},
            },
        }

    found = {name: await db[name].find(flt).to_list(length=5000) for name, flt in child_filters(ids).items()}
    print("\nRelated rows to remove: " + ", ".join(f"{name}={len(rows)}" for name, rows in found.items()))
    print(f"Kept (not deleted): audit_logs={await db.audit_logs.count_documents({'target_id': {'$in': ids}})}")

    from app.services.booking_service import BookingService

    booking_service = BookingService(db)
    # What gets handed back. The seat: only bookings that RECORD holding one
    # (holds_seat) — a row from before ownership was recorded is judged by
    # the same rule the app's own backfill applies, never guessed here.
    coupons = [b for b in bookings if b.get("coupon_code")]
    passes = [b for b in bookings if b.get("subscription_id") and b.get("subscription_consumption")]
    visits: dict[str, list[dict]] = {}
    for b in bookings:
        visits.setdefault(b.get("booking_group_id") or str(b["_id"]), []).append(b)
    legacy_holders: set[str] = set()
    for cars in visits.values():
        legacy_holders |= booking_service._legacy_seat_holders(cars)
    seats = sum(1 for b in bookings if b.get("holds_seat") is True or str(b["_id"]) in legacy_holders)
    charges = sum(1 for b in bookings if float(b.get("cancellation_charge") or 0) > 0)
    print(f"Handed back: coupon uses={len(coupons)}, subscription passes={len(passes)}, slot seats={seats}, "
          f"bookings carrying cancellation charges={charges}")

    if problems:
        print("\nREFUSING — nothing was changed:")
        for p in problems:
            print("  - " + p)
        await close_mongo_connection()
        return 1

    if not do_delete:
        print("\nDry run — nothing was changed. Check the list above, then add --delete.")
        await close_mongo_connection()
        return 0

    backup = Path(f"deleted-bookings-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    backup.write_text(json_util.dumps({"bookings": bookings, **found}, indent=2))
    print(f"\nBackup written: {backup.resolve()}")

    from app.services.coupon_service import CouponService
    from app.services.subscription_service import UserSubscriptionService

    deleted = 0
    # One VISIT at a time, in ONE transaction: the cars are marked deleted,
    # the seat goes back from the car that records holding it (the app's own
    # _release_seat — never to a car of this same visit), its late-
    # cancellation charges go back on the account, and the rows go. Only
    # after that commits are the coupon use and pass wash handed back — a
    # re-run can never find (and hand back) the same booking twice. If
    # anything goes wrong the script stops right there.
    for visit_key, cars in visits.items():
        numbers = ", ".join(c["booking_number"] for c in cars)
        ids_ = [str(c["_id"]) for c in cars]

        async def _do(session, cars=cars, ids_=ids_):
            await booking_service._touch_customer(cars[0].get("customer_id"), session)
            await booking_service._backfill_seats(cars, session)
            for car in cars:
                await db.bookings.update_one({"_id": car["_id"]}, {"$set": {"is_deleted": True}}, session=session)
            for car in cars:
                await booking_service._release_seat(car, session)
                await booking_service.charges.release_for_booking(session, car, reason="deleted")
            # Customer-wallet money on these rows (credit spent on them, a
            # previous balance they carried — spec 1.1) goes back to the
            # wallet before the rows go; a cancelled row already settled is
            # skipped by MoneyService itself.
            # A job that was DONE keeps its money (deleting its record is not a refund).
            unfinished = [c for c in cars if c.get("status") != "completed"]
            if any(float(c.get("wallet_applied") or 0) > 0 or float(c.get("wallet_due_carried") or 0) > 0 for c in unfinished):
                await booking_service.money.on_booking_cancelled(
                    [c["_id"] for c in unfinished], charge_amount=0, actor={"id": "system", "role": "system", "name": "Delete script"},
                    session=session, reason="booking deleted",
                )
            for name, flt in child_filters(ids_).items():
                await db[name].delete_many(flt, session=session)
            await db.bookings.delete_many({"_id": {"$in": [c["_id"] for c in cars]}}, session=session)

        try:
            async with await db.client.start_session() as session:
                await session.with_transaction(_do)
        except Exception as exc:  # noqa: BLE001
            print(f"\nSTOPPED at {numbers}: {type(exc).__name__}: {exc}")
            print(f"{deleted} booking(s) before it were fully deleted; {numbers} and the rest were left as they are. Backup: {backup.resolve()}")
            await close_mongo_connection()
            return 1
        for b in cars:
            bid = str(b["_id"])
            try:
                # Not a wash already handed back by its cancel, nor one a
                # late cancel used up (consumption_forfeited, spec 1.2).
                if (
                    b.get("subscription_id") and b.get("subscription_consumption")
                    and not b.get("consumption_restored") and not b.get("consumption_forfeited")
                ):
                    await UserSubscriptionService(db).restore_consumption(b["subscription_id"], b["subscription_consumption"])
                if b.get("coupon_code"):
                    await CouponService(db).reverse_usage(b["coupon_code"], b["customer_id"], bid)
            except Exception as exc:  # noqa: BLE001 — the booking is gone; say exactly what to hand back by hand
                print(f"  WARNING {b['booking_number']} deleted, but its pass/coupon could not be handed back: {type(exc).__name__}: {exc}")
            deleted += 1
            print(f"  deleted {b['booking_number']}")
    print(f"\nDeleted {deleted} of {len(bookings)} booking(s).")
    print("Still there afterwards:", await db.bookings.count_documents({"booking_number": {"$in": wanted}}), "(should be 0)")
    await close_mongo_connection()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Permanently delete bookings (dry run unless --delete).")
    parser.add_argument("numbers", nargs="+", help="e.g. BK0009 or a range BK0001-BK0007 (any number of them)")
    parser.add_argument("--phone", help="optional: every booking must belong to this customer phone")
    parser.add_argument("--delete", action="store_true", help="actually delete (default is a dry run)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.numbers, args.phone, args.delete)))


if __name__ == "__main__":
    main()
