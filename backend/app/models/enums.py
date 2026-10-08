"""
Central place for every enum used in the domain. Keeping these in one
module prevents drift between models, schemas, and services.
"""
from enum import Enum


class UserRole(str, Enum):
    CUSTOMER = "customer"
    CAPTAIN = "captain"
    MANAGER = "manager"
    ADMIN = "admin"


class UserStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"


class BookingStatus(str, Enum):
    # Created, its slot is HELD, but it is not a real booking yet: the
    # customer chose to pay online and hasn't finished paying. It is in no
    # queue, no captain can be assigned, and nobody has been notified.
    # A verified payment (or the customer switching to cash) promotes it to
    # PENDING; the unpaid-booking sweep cancels it if neither happens.
    AWAITING_PAYMENT = "awaiting_payment"
    PENDING = "pending"
    ASSIGNED = "assigned"
    CAPTAIN_ON_THE_WAY = "captain_on_the_way"
    SERVICE_STARTED = "service_started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"


class BookingPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PaymentStatus(str, Enum):
    # Nothing paid yet, something due (see app/services/booking_money.py).
    PENDING = "pending"
    # Something paid (online / QR / cash), something still due — after an
    # edit, an on-site add-on, or a partial payment.
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    FAILED = "failed"
    # Paid online, then cancelled/deleted: the money is owed back and the
    # booking sits on the admin refund queue (PaymentService.flag_refund_due).
    REFUND_DUE = "refund_due"
    REFUNDED = "refunded"


class PaymentMethod(str, Enum):
    CASH = "cash"
    # Real online payment via Razorpay Standard Checkout — the booking is
    # created payment_status=pending and flips to paid only after the
    # signature-verified payment (see PaymentService.verify_payment).
    ONLINE = "online"
    # Legacy value from before the gateway existed — kept ONLY so old
    # stored bookings still parse; nothing writes it anymore.
    ONLINE_PLACEHOLDER = "online_placeholder"
    SUBSCRIPTION = "subscription"


class CustomerChargeStatus(str, Enum):
    # On the customer's account, waiting for their next booking.
    OPEN = "open"
    # Added to a booking's total (applied_to_booking_id). Goes back to OPEN
    # if that booking is cancelled or deleted before it is paid.
    APPLIED = "applied"
    # A manager/admin removed it (reduced to ₹0).
    WAIVED = "waived"
    # Charged to the customer's wallet (the wallet model, founder
    # 2026-10-07): a debit row on customer_wallet_ledger settled it. A
    # reduction is credited back to the wallet.
    SETTLED = "settled"


class CustomerWalletEntryKind(str, Enum):
    """Why a customer-wallet ledger row exists (customer_wallet_ledger.kind).
    The sign of the row's amount says credit (+) or debit (−)."""
    # A cancelled booking: what was paid back, net of the cancellation charge
    # (negative when the charge was bigger than what was paid).
    CANCELLATION = "cancellation"
    # An unpaid booking cancelled late: the charge.
    CANCELLATION_CHARGE = "cancellation_charge"
    # Open late-cancellation charges moved onto the wallet (migration).
    CHARGE_MIGRATED = "charge_migrated"
    # A manager/admin reduced or waived a charge already on the wallet.
    CHARGE_REDUCED = "charge_reduced"
    # A paid booking edited to a lower price.
    PRICE_REDUCED = "price_reduced"
    # Paid more than was due (online and cash for the same thing, a link for
    # an old amount, a payment after a cancel).
    OVERPAYMENT = "overpayment"
    # Wallet credit spent on a booking at creation / returned when that
    # booking is cancelled.
    BOOKING_PAYMENT = "booking_payment"
    # A negative balance carried into a booking was paid with it.
    PREVIOUS_BALANCE_PAID = "previous_balance_paid"
    # The manager transferred money back to the customer outside the app.
    PAYOUT = "payout"
    ADJUSTMENT = "adjustment"
    # One car of a paid custom plan refunded to the wallet (PLANS-2,
    # CustomPlanService.refund_car; meta.custom_plan_id names the cart).
    REFUND = "refund"


class CancellationChargeTier(str, Enum):
    """The late-cancellation policy table (founder, 2026-10-07). More than
    the lock window (CUSTOMER_CANCEL_LOCK_HOURS) before the slot is free."""
    FREE = "free"
    ONE_TO_FOUR_HOURS = "1_to_4h"
    UNDER_ONE_HOUR = "under_1h"
    AFTER_CAPTAIN_LEFT = "after_captain_left"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    # A custom-plan renewal paid while the car's old pass is still live:
    # it starts the day after the old pass's Last Booking Day and is
    # promoted to ACTIVE at its start (subscription_service.PASS_SCHEDULED).
    SCHEDULED = "scheduled"


class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class ComplaintStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ComplaintPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class CouponType(str, Enum):
    FLAT = "flat"
    PERCENTAGE = "percentage"


class NotificationType(str, Enum):
    BOOKING = "booking"
    SUBSCRIPTION = "subscription"
    PROMOTION = "promotion"
    SYSTEM = "system"
    COMPLAINT = "complaint"


class InventoryUnit(str, Enum):
    LITRE = "litre"
    ML = "ml"
    PIECE = "piece"
    KG = "kg"
    GRAM = "gram"
    BOTTLE = "bottle"


class LeaveStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AttendanceStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    HALF_DAY = "half_day"
    ON_LEAVE = "on_leave"


class WalletTransactionType(str, Enum):
    CREDIT = "credit"          # platform pays captain (online booking payout)
    DEBIT = "debit"             # captain owes platform (cash booking platform cut)
    TOPUP = "topup"              # captain adds own money to meet minimum balance
    WITHDRAWAL = "withdrawal"    # captain cashes out to bank
    ADJUSTMENT = "adjustment"    # manual admin correction


class WithdrawalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class CaptainAvailability(str, Enum):
    AVAILABLE = "available"
    ON_JOB = "on_job"
    OFFLINE = "offline"
