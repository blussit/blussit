"""Discounts are whole rupees: percentage coupons round half-up, and a
discount shared across cars splits into whole rupees that add up exactly."""
import pytest

from app.schemas.booking_schema import ManagerLogBookingRequest
from app.services.coupon_service import CouponService
from app.utils.money import round_rupees, split_whole_rupees


def test_round_rupees_is_half_up():
    assert round_rupees(34.5) == 35
    assert round_rupees(34.49) == 34
    assert round_rupees(0.5) == 1  # Python's round() would say 0
    assert round_rupees(None) == 0


@pytest.mark.parametrize(
    "amount,weights,caps,expected",
    [
        (50, [349, 499], [349, 499], [21, 29]),
        (100, [349, 349, 349], None, [34, 33, 33]),
        (101, [399, 399], [399, 399], [51, 50]),
        (500, [100, 499], [100, 499], [83, 417]),
        (150, [100, 499], [100, 499], [25, 125]),
        (0, [349, 499], None, [0, 0]),
    ],
)
def test_split_is_whole_rupees_and_adds_up(amount, weights, caps, expected):
    parts = split_whole_rupees(amount, weights, caps)
    assert parts == expected
    assert sum(parts) == amount
    assert all(isinstance(p, int) for p in parts)


def test_split_respects_each_cars_price():
    # ₹590 off a ₹100 + ₹499 visit: the cheap car can't go below zero.
    parts = split_whole_rupees(590, [100, 499], [100, 499])
    assert parts[0] <= 100 and parts[1] <= 499 and sum(parts) == 590


def test_percentage_coupon_is_whole_rupees():
    ten_percent = {"coupon_type": "percentage", "value": 10}
    assert CouponService._standard_discount(ten_percent, 349) == 35  # not 34.9
    assert CouponService._standard_discount(ten_percent, 344) == 34  # 34.4
    capped = {"coupon_type": "percentage", "value": 15, "max_discount_amount": 50}
    assert CouponService._standard_discount(capped, 999) == 50
    flat = {"coupon_type": "flat", "value": 500}
    assert CouponService._standard_discount(flat, 349) == 349  # never more than the bill


def test_manager_typed_discount_rounds_to_whole_rupees():
    from app.schemas.booking_schema import QuickBookingLine

    req = ManagerLogBookingRequest(
        customer_name="Round Ravi", customer_phone="9666600099",
        lines=[QuickBookingLine(vehicle_type="x", quantity=1, service_ids=["y"])],
        scheduled_date="2026-09-27", service_time="10:30", address_line="Lane 1", discount_amount=50.6,
    )
    assert req.discount_amount == 51
