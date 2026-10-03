from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.exceptions import ForbiddenException
from app.core.responses import paginated, success
from app.schemas.booking_schema import (
    BookingGroupCreateRequest,
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingCreateRequest,
    BookingQuoteRequest,
    BookingRescheduleRequest,
    BookingUpdateDetailsRequest,
    CaptainCancelRequest,
    HeadingRequest,
    ManagerBookingCreateRequest,
    ManagerLogBookingRequest,
    ManagerMarkDoneRequest,
    PhotoCaptureRequest,
    PriorityUpdateRequest,
    QuickBookingRequest,
    ReassignCaptainRequest,
    ReportRiskRequest,
    ResolveIssueRequest,
    VerifyVehicleRequest,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.booking_service import BookingService, redact_for_captain
from app.services.purchase_confirmation_service import PurchaseConfirmationService
from app.services.staff_directory_service import StaffDirectoryService


class BookingController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.service = BookingService(db)
        self.audit = AuditService(db)
        self.confirmations = PurchaseConfirmationService(db)

    async def _quick_result_with_ticket(self, result: dict, customer_id: str) -> dict:
        # The public /thank-you page reads this ticket, never a raw booking
        # id — same as the older self-service create paths.
        bookings = result.get("bookings") or []
        service_names = [
            b.get("combo_name") or ", ".join(b.get("service_names") or [])
            for b in bookings
            if b.get("combo_name") or b.get("service_names")
        ]
        if bookings:
            result["confirmation_token"] = await self.confirmations.issue(
                "booking",
                bookings[0]["id"],
                customer_id,
                {
                    "booking_number": " + ".join(result.get("booking_numbers") or []),
                    "scheduled_date": result.get("scheduled_date"),
                    "scheduled_slot": result.get("scheduled_slot"),
                    "service_label": " + ".join(service_names) or None,
                    "service_code": result.get("service_code"),
                    "payment_link": result.get("payment_link"),
                    "awaiting_payment": result.get("awaiting_payment"),
                    "total_amount": result.get("total_amount"),
                },
            )
        return result

    async def quick_create(self, current_user: CurrentUser | None, payload: QuickBookingRequest):
        """The no-login booking. A signed-in CUSTOMER books under their own
        account (the typed name/phone are ignored in favour of the
        profile); anyone else is found-or-created by phone, but only after
        proving the phone with the confirm-booking OTP."""
        auth = AuthService(self.db)
        signed_in_customer = current_user is not None and current_user.role == "customer"
        if signed_in_customer:
            customer = await auth.users.find_by_id(current_user.id)
            if not customer:
                customer = await auth.ensure_customer_by_phone(payload.customer_phone, payload.customer_name)
            elif not customer.get("phone"):
                # A Google sign-in account has no phone yet — the number
                # typed into the booking becomes the account's, unless it
                # already belongs to someone else (then they must log in
                # with that number instead of quietly taking it over).
                # It must be PROVEN first (same OTP as a guest booking):
                # an unverified claim let anyone attach a stranger's
                # not-yet-registered number to their own Google account,
                # so the real owner's later bookings and OTP logins landed
                # in the claimer's account (and fresh numbers meant endless
                # first-wash prices).
                from app.core.exceptions import BadRequestException
                from app.utils.phone import BUSINESS_NUMBER_MESSAGE, is_business_whatsapp_number

                if is_business_whatsapp_number(payload.customer_phone):
                    raise BadRequestException(BUSINESS_NUMBER_MESSAGE)
                other = await auth.users.find_by_phone(payload.customer_phone)
                if other and str(other["_id"]) != str(customer["_id"]):
                    raise BadRequestException("This mobile number already has an account — log out and log in with that number to book.")
                if not (payload.phone_otp or payload.phone_access_token):
                    await auth.require_phone_proof(payload.customer_phone, None, None)  # the "verify your number" refusal
                await self.service.precheck_quick_booking(payload, source="app")
                await auth.require_phone_proof(payload.customer_phone, payload.phone_otp, payload.phone_access_token)
                from datetime import timezone as _tz

                await auth.users.update_by_id(
                    str(customer["_id"]),
                    {"phone": payload.customer_phone, "phone_verified": True, "phone_verified_at": datetime.now(_tz.utc)},
                )
                customer["phone"] = payload.customer_phone
        else:
            if not (payload.phone_otp or payload.phone_access_token):
                await auth.require_phone_proof(payload.customer_phone, None, None)  # the "verify your number" refusal
            # Checking a code spends it — refuse what we can first, so a
            # corrected retry doesn't need a fresh code.
            await self.service.precheck_quick_booking(payload, source="app")
            await auth.require_phone_proof(payload.customer_phone, payload.phone_otp, payload.phone_access_token)
            customer = await auth.ensure_customer_by_phone(payload.customer_phone, payload.customer_name)
        result = await self.service.create_quick_booking(payload, customer=customer, source="app")
        result = await self._quick_result_with_ticket(result, str(customer["_id"]))
        return success(result, "Booking confirmed" if not result.get("awaiting_payment") else "Finish paying to confirm your booking")

    async def _ensure_manager_books_own_center(self, current_user: CurrentUser, *, address_id: str | None, address=None) -> None:
        """A manager books into THEIR center only — the booking's center is
        resolved from the customer's address, so without this a manager
        could fill another center's slots (and fire its alerts) just by
        typing an address in its area. Admin books anywhere."""
        if current_user.role == "admin":
            return
        center_id = await self.service.staff_booking_center_id(address_id=address_id, address=address)
        if center_id is not None and center_id != current_user.service_center_id:
            raise ForbiddenException("This address is served by another service center — ask an admin to book it there.")

    async def manager_quick_create(self, current_user: CurrentUser, payload: QuickBookingRequest):
        """Same quick shape, on a customer's behalf — the phone-in booking."""
        await self._ensure_manager_books_own_center(current_user, address_id=payload.address_id, address=payload.address)
        customer = await AuthService(self.db).ensure_customer_by_phone(payload.customer_phone, payload.customer_name)
        result = await self.service.create_quick_booking(payload, customer=customer, source="staff", allow_pinless=True)
        for b in result.get("bookings") or []:
            await self.audit.log_action(
                current_user.id, current_user.role, "MANAGER_CREATE_BOOKING", "bookings", b["id"], {"customer_id": str(customer["_id"])}
            )
        if not result.get("awaiting_payment"):
            return success(result, "Booking created")
        if result.get("payment_link"):
            return success(result, "Payment link sent — the booking confirms once it's paid")
        return success(result, "Booking saved — it confirms once paid online")

    async def manager_log_completed(self, current_user: CurrentUser, payload: ManagerLogBookingRequest):
        """A job the manager already did himself, saved directly as done."""
        result = await self.service.create_manager_logged_visit(
            payload, manager_id=current_user.id, manager_center_id=current_user.service_center_id
        )
        for b in result.get("bookings") or []:
            await self.audit.log_action(
                current_user.id, current_user.role, "MANAGER_LOG_COMPLETED", "bookings", b["id"],
                {"customer_id": result.get("customer_id"), "send_whatsapp": payload.send_whatsapp, "discount_amount": payload.discount_amount},
            )
        return success(result, "Job logged as done")

    async def manager_mark_done(self, current_user: CurrentUser, booking_id: str, payload: ManagerMarkDoneRequest):
        result = await self.service.manager_mark_done(
            booking_id, current_user.id, current_user.role, current_user.service_center_id, payload.send_whatsapp
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "MANAGER_MARK_DONE", "bookings", booking_id,
            {"send_whatsapp": payload.send_whatsapp, "completed": result["completed"], "booking_numbers": result["booking_numbers"]},
        )
        return success(result, "Marked as done")

    async def create(self, current_user: CurrentUser, payload: BookingCreateRequest):
        """The older one-car request shape — priced, planned and parked by the
        same rules as /bookings/quick (see create_self_service_booking)."""
        result = await self.service.create_self_service_booking(current_user.id, payload)
        enriched = await self.service.get_booking(result["id"])
        service_label = enriched.get("combo_name") or ", ".join(enriched.get("service_names") or [])
        # The public /thank-you page reads this ticket, never the raw
        # booking id (see PurchaseConfirmationModel) — issued here, right
        # after a genuinely successful self-service booking, not by the
        # manager-create path (managers never leave their own dashboard).
        result["confirmation_token"] = await self.confirmations.issue(
            "booking", result["id"], current_user.id,
            {
                "booking_number": result.get("booking_number"),
                "scheduled_date": result.get("scheduled_date"),
                "scheduled_slot": result.get("scheduled_slot"),
                "service_label": service_label or None,
                "service_code": result.get("service_code"),
                "payment_link": result.get("payment_link"),
                "awaiting_payment": result.get("awaiting_payment"),
                "total_amount": result.get("total_amount"),
            },
        )
        return success(result, "Booking created successfully" if not result.get("awaiting_payment") else "Finish paying to confirm your booking")

    async def quote(self, current_user: CurrentUser | None, payload: BookingQuoteRequest):
        """The bill before booking, for every booking screen. A signed-in
        customer is quoted as themselves (their plans, their first-time
        status); staff quote the customer who owns customer_phone; an
        anonymous visitor gets first-time status from the phone but never
        anyone's plans or saved addresses."""
        from app.core.exceptions import BadRequestException
        from app.utils.timezone import to_ist

        role = current_user.role if current_user else None
        staff = role in ("manager", "admin")
        customer: dict | None = None
        phone = payload.customer_phone
        users = self.service.user_repo
        if role == "customer":
            customer = await users.find_by_id(current_user.id)
            phone = (customer or {}).get("phone") or phone
        elif staff and phone:
            found = await users.find_by_phone(phone)
            customer = found if found and found.get("role") == "customer" else None
        customer_id = str(customer["_id"]) if customer else None

        address: dict | None = None
        if payload.address_id and customer_id:
            saved = await self.service.address_repo.find_by_id(payload.address_id)
            if saved and saved.get("owner_id") == customer_id:
                address = saved
        elif payload.address and (payload.address.latitude is not None or payload.address.pincode):
            address = payload.address.model_dump()
            address["pincode"] = address.get("pincode") or ""

        log_mode = staff and payload.mode == "log"
        as_of = None
        if log_mode and payload.scheduled_date and payload.service_time:
            try:
                as_of = to_ist(datetime.strptime(f"{payload.scheduled_date} {payload.service_time}", "%Y-%m-%d %H:%M"))
            except ValueError:
                raise BadRequestException("Enter a valid date and time.")
        quote = await self.service.quote_visit(
            customer_id=customer_id,
            phone=phone,
            lines=payload.lines,
            address=address,
            coupon_code=None if log_mode else (payload.coupon_code or "").strip().upper() or None,
            source="staff" if staff else "app",
            log_mode=log_mode,
            as_of=as_of,
        )
        return success(quote)

    async def manager_create(self, current_user: CurrentUser, payload: ManagerBookingCreateRequest):
        await self._ensure_manager_books_own_center(current_user, address_id=payload.address_id, address=payload.new_address)
        result = await self.service.create_booking_for_customer(current_user.id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "MANAGER_CREATE_BOOKING", "bookings", result["id"], {"customer_id": payload.customer_id})
        return success(result, "Booking created successfully")

    async def report_risk(self, current_user: CurrentUser, booking_id: str, payload: ReportRiskRequest):
        result = await self.service.report_risk(booking_id, current_user.id, payload.note)
        return success(redact_for_captain(result), "Manager notified")

    async def eligible_captains(self, current_user: CurrentUser, db: AsyncIOMotorDatabase, booking_id: str):
        return success(
            await StaffDirectoryService(db).eligible_captains_for_booking(
                booking_id, db, current_user.role, current_user.service_center_id
            )
        )

    async def get(self, current_user: CurrentUser, booking_id: str):
        return success(
            await self.service.get_booking_with_history(
                booking_id, current_user.id, current_user.role, current_user.service_center_id
            )
        )

    async def list_my_bookings(self, current_user: CurrentUser, status: str | None, pagination: PaginationParams):
        items, total = await self.service.list_for_customer(current_user.id, status, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_my_jobs(self, current_user: CurrentUser, status: str | None, pagination: PaginationParams, scope: str | None = None):
        items, total = await self.service.list_for_captain(current_user.id, status, pagination.page, pagination.page_size, scope=scope)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_for_center(
        self, current_user: CurrentUser, service_center_id: str, extra_filters: dict, pagination: PaginationParams,
        queue: list[dict] | None = None, sort: str | None = None,
    ):
        items, total = await self.service.list_for_center(
            service_center_id, extra_filters, pagination.page, pagination.page_size, current_user.role, current_user.service_center_id,
            queue=queue, sort=sort,
        )
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_subscribers_for_center(self, current_user: CurrentUser, service_center_id: str):
        return success(await self.service.list_subscribers_for_center(service_center_id, current_user.role, current_user.service_center_id))

    async def list_all(self, filters: dict, pagination: PaginationParams):
        items, total = await self.service.list_all(filters, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_recycle_bin(self, pagination: PaginationParams):
        items, total = await self.service.list_recycle_bin(pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def soft_delete(self, current_user: CurrentUser, booking_id: str):
        result = await self.service.soft_delete_booking(booking_id, current_user.id)
        await self.audit.log_action(current_user.id, current_user.role, "DELETE_BOOKING", "bookings", booking_id, result)
        return success(result, "Moved to recycle bin")

    async def restore(self, current_user: CurrentUser, booking_id: str):
        result = await self.service.restore_booking(booking_id)
        await self.audit.log_action(current_user.id, current_user.role, "RESTORE_BOOKING", "bookings", booking_id, result)
        return success(result, "Restored")

    async def permanently_delete(self, current_user: CurrentUser, booking_id: str, force: bool):
        result = await self.service.permanently_delete_booking(booking_id, force=force)
        await self.audit.log_action(current_user.id, current_user.role, "PERMANENTLY_DELETE_BOOKING", "bookings", booking_id, {**result, "force": force})
        return success(result, "Permanently deleted")

    async def assign_captain(self, current_user: CurrentUser, booking_id: str, payload: BookingAssignCaptainRequest):
        result = await self.service.assign_captain(booking_id, payload, current_user.id, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "ASSIGN_CAPTAIN", "bookings", booking_id, {"captain_id": payload.captain_id})
        return success(result, "Captain assigned successfully")

    async def reassign_captain(self, current_user: CurrentUser, booking_id: str, payload: ReassignCaptainRequest):
        result = await self.service.reassign_captain(booking_id, payload, current_user.id, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "REASSIGN_CAPTAIN", "bookings", booking_id, {"captain_id": payload.captain_id})
        return success(result, "Booking reassigned successfully")

    async def self_assign(self, current_user: CurrentUser, booking_id: str):
        result = await self.service.self_assign(booking_id, current_user.id, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "SELF_ASSIGN_BOOKING", "bookings", booking_id, None)
        return success(result, "Assigned to you")

    async def captain_cancel(self, current_user: CurrentUser, booking_id: str, payload: CaptainCancelRequest):
        result = await self.service.captain_cancel(booking_id, payload, current_user.id)
        return success(redact_for_captain(result), "Booking released back to the queue")

    async def start_heading(self, current_user: CurrentUser, booking_id: str, payload: HeadingRequest):
        result = await self.service.start_heading(booking_id, payload, current_user.id)
        return success(redact_for_captain(result), "Heading to customer")

    async def verify_vehicle(self, current_user: CurrentUser, booking_id: str, payload: VerifyVehicleRequest):
        result = await self.service.verify_vehicle(booking_id, payload, current_user.id)
        return success(redact_for_captain(result), "Vehicle verified")

    async def resolve_issue(self, current_user: CurrentUser, booking_id: str, payload: ResolveIssueRequest):
        result = await self.service.resolve_issue(booking_id, current_user.id, payload.note, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "RESOLVE_BOOKING_ISSUE", "bookings", booking_id, {"note": payload.note})
        return success(result, "Issue marked resolved")

    async def update_priority(self, current_user: CurrentUser, booking_id: str, payload: PriorityUpdateRequest):
        result, old_priority = await self.service.update_priority(
            booking_id, payload.priority.value, current_user.id, current_user.role, current_user.service_center_id
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "UPDATE_BOOKING_PRIORITY", "bookings", booking_id,
            {"old": old_priority, "new": payload.priority.value},
        )
        return success(result, "Priority updated")

    async def capture_before_photo(self, current_user: CurrentUser, booking_id: str, payload: PhotoCaptureRequest):
        result = await self.service.capture_before_photo(booking_id, payload, current_user.id)
        return success(redact_for_captain(result), "Service started")

    async def capture_after_photo(self, current_user: CurrentUser, booking_id: str, payload: PhotoCaptureRequest):
        result = await self.service.capture_after_photo_and_complete(booking_id, payload, current_user.id)
        return success(redact_for_captain(result), "Service completed")

    async def get_group(self, current_user: CurrentUser, booking_group_id: str):
        return success(
            await self.service.get_booking_group(
                booking_group_id, current_user.id, current_user.role, current_user.service_center_id
            )
        )

    async def switch_group_to_cash(self, current_user: CurrentUser, booking_group_id: str):
        result = await self.service.switch_group_to_cash(booking_group_id, current_user.id)
        await self.audit.log_action(
            current_user.id, current_user.role, "VISIT_SWITCHED_TO_CASH", "bookings", booking_group_id, None
        )
        return success(result, "Visit confirmed — pay the captain at your doorstep")

    async def cancel_group(self, current_user: CurrentUser, booking_group_id: str, payload: BookingCancelRequest):
        result = await self.service.cancel_booking_group(
            booking_group_id, payload, current_user.id, current_user.role, current_user.service_center_id
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "CANCEL_BOOKING_GROUP", "bookings", booking_group_id,
            {"vehicles": result["cancelled_count"]},
        )
        return success(result, "Visit cancelled")

    async def assign_group(self, current_user: CurrentUser, booking_group_id: str, payload: BookingAssignCaptainRequest):
        result = await self.service.assign_captain_to_group(
            booking_group_id, payload, current_user.id, current_user.role, current_user.service_center_id
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "ASSIGN_CAPTAIN_GROUP", "bookings", booking_group_id,
            {"captain_id": payload.captain_id, "vehicles": result["assigned_count"]},
        )
        return success(result, f"{result['assigned_count']} vehicles assigned")

    async def create_group(self, current_user: CurrentUser, payload: BookingGroupCreateRequest):
        result = await self.service.create_self_service_group(current_user.id, payload)
        enriched = await self.service.get_booking_group(
            result["booking_group_id"], current_user.id, current_user.role, current_user.service_center_id
        )
        service_names = [
            b.get("combo_name") or ", ".join(b.get("service_names") or [])
            for b in enriched
            if b.get("combo_name") or b.get("service_names")
        ]
        service_label = " + ".join(service_names)
        result["confirmation_token"] = await self.confirmations.issue(
            "booking",
            result["bookings"][0]["id"],
            current_user.id,
            {
                "booking_number": " + ".join(b.get("booking_number", "") for b in result["bookings"]).strip(" +"),
                "scheduled_date": result.get("scheduled_date"),
                "scheduled_slot": result.get("scheduled_slot"),
                "service_label": service_label or None,
                "service_code": result.get("service_code"),
                "payment_link": result.get("payment_link"),
                "awaiting_payment": result.get("awaiting_payment"),
                "total_amount": result.get("total_amount"),
            },
        )
        await self.audit.log_action(
            current_user.id, current_user.role, "CREATE_BOOKING_GROUP", "bookings",
            result["booking_group_id"], {"vehicles": result["vehicle_count"]},
        )
        return success(result, f"{result['vehicle_count']} vehicles booked for one visit")

    async def switch_to_cash(self, current_user: CurrentUser, booking_id: str):
        result = await self.service.switch_to_cash(booking_id, current_user.id)
        await self.audit.log_action(
            current_user.id, current_user.role, "BOOKING_SWITCHED_TO_CASH", "bookings", booking_id, None
        )
        return success(result, "Booking confirmed — pay the captain at your doorstep")

    async def cancel(self, current_user: CurrentUser, booking_id: str, payload: BookingCancelRequest):
        result = await self.service.cancel_booking(booking_id, payload, current_user.id, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "CANCEL_BOOKING", "bookings", booking_id, {"reason": payload.reason})
        return success(result, "Booking cancelled successfully")

    async def reschedule(self, current_user: CurrentUser, booking_id: str, payload: BookingRescheduleRequest):
        result = await self.service.reschedule_booking(booking_id, payload, current_user.id, current_user.role, current_user.service_center_id)
        await self.audit.log_action(
            current_user.id, current_user.role, "RESCHEDULE_BOOKING", "bookings", booking_id,
            {"new_date": payload.scheduled_date.isoformat(), "new_slot": payload.scheduled_slot},
        )
        return success(result, "Booking rescheduled successfully")

    async def update_details(self, current_user: CurrentUser, booking_id: str, payload: BookingUpdateDetailsRequest):
        result = await self.service.update_details(booking_id, payload, current_user.role, current_user.service_center_id)
        await self.audit.log_action(
            current_user.id, current_user.role, "UPDATE_BOOKING_DETAILS", "bookings", booking_id,
            payload.model_dump(exclude_unset=True),
        )
        return success(result, "Booking updated")
