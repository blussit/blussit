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
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
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


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PAUSED = "paused"


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
