"""The itemised bill (BookingService._bill_view): rows always add up to the total."""
from app.services.booking_service import BookingService


def _bill(booking, services, combo_name=None):
    return BookingService._bill_view(BookingService.__new__(BookingService), booking, services, combo_name)


def _sum(bill):
    rows = sum(l["amount"] for l in bill["lines"]) + bill["adjustment"] + sum(e["amount"] for e in bill["on_site"])
    rows -= (bill["discount"] or {}).get("amount", 0) + bill["manager_discount"]
    rows += (bill["travel"] or {}).get("amount", 0) + bill["carried"] + bill["cancellation"] + (bill["tip"] or {}).get("amount", 0) + bill["other"]
    return round(rows, 2)


SERVICES = {
    "wash": {"_id": "wash", "name": "Star Wash", "price": 449, "discounted_price": 349, "is_addon": False},
    "polish": {"_id": "polish", "name": "Car Polish", "price": 100, "is_addon": True},
}


def test_coupon_add_on_distance_and_tip_add_up_to_the_total():
    booking = {
        "vehicle_type": "t1", "service_ids": ["wash", "polish"], "first_time_eligible": False,
        "subtotal": 549, "discount_amount": 50, "coupon_code": "SAVE50", "travel_charge": 4, "travel_charge_km": 6.72,
        "tip_amount": 20, "tip_method": "cash", "total_amount": 523,
    }
    bill = _bill(booking, SERVICES)
    assert [(l["name"], l["amount"], l["is_addon"]) for l in bill["lines"]] == [("Star Wash", 449, False), ("Car Polish", 100, True)]
    assert bill["discount"] == {"kind": "coupon", "code": "SAVE50", "amount": 50}
    assert bill["travel"]["amount"] == 4 and bill["tip"]["amount"] == 20
    assert bill["other"] == 0 and bill["adjustment"] == 0 and _sum(bill) == 523


def test_first_wash_price_shows_the_regular_price_beside_it():
    bill = _bill({"vehicle_type": "t1", "service_ids": ["wash"], "first_time_eligible": True, "subtotal": 349, "total_amount": 349}, SERVICES)
    assert bill["lines"][0]["amount"] == 349 and bill["lines"][0]["regular_amount"] == 449 and _sum(bill) == 349


def test_a_changed_catalogue_price_becomes_one_adjustment_row_not_a_wrong_bill():
    bill = _bill({"vehicle_type": "t1", "service_ids": ["wash"], "subtotal": 399, "total_amount": 399}, SERVICES)
    assert bill["adjustment"] == -50 and _sum(bill) == 399


def test_manager_discount_is_not_counted_twice_and_plan_cover_is_labelled():
    logged = {"vehicle_type": "t1", "service_ids": ["wash"], "subtotal": 449, "discount_amount": 49, "manager_discount": 49, "total_amount": 400}
    bill = _bill(logged, SERVICES)
    assert bill["discount"] is None and bill["manager_discount"] == 49 and _sum(bill) == 400
    plan = {"vehicle_type": "t1", "service_ids": ["wash", "polish"], "subscription_id": "s1", "subtotal": 549, "discount_amount": 449, "total_amount": 100}
    bill = _bill(plan, SERVICES)
    assert bill["discount"]["kind"] == "plan" and _sum(bill) == 100
