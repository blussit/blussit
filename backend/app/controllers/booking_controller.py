from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, PaginationParams
from app.core.responses import paginated, success
from app.schemas.booking_schema import (
    BookingAssignCaptainRequest,
    BookingCancelRequest,
    BookingCreateRequest,
    BookingRescheduleRequest,
    CaptainCancelRequest,
    HeadingRequest,
    ManagerBookingCreateRequest,
    PhotoCaptureRequest,
    PriorityUpdateRequest,
    ReassignCaptainRequest,
    ReportRiskRequest,
    ResolveIssueRequest,
    VerifyVehicleRequest,
)
from app.services.audit_service import AuditService
from app.services.booking_service import BookingService
from app.services.purchase_confirmation_service import PurchaseConfirmationService
from app.services.staff_directory_service import StaffDirectoryService


class BookingController:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.service = BookingService(db)
        self.audit = AuditService(db)
        self.confirmations = PurchaseConfirmationService(db)

    async def create(self, current_user: CurrentUser, payload: BookingCreateRequest):
        result = await self.service.create_booking(current_user.id, payload)
        # The public /thank-you page reads this ticket, never the raw
        # booking id (see PurchaseConfirmationModel) — issued here, right
        # after a genuinely successful self-service booking, not by the
        # manager-create path (managers never leave their own dashboard).
        result["confirmation_token"] = await self.confirmations.issue(
            "booking", result["id"], current_user.id,
            {"booking_number": result.get("booking_number"), "scheduled_date": result.get("scheduled_date"), "scheduled_slot": result.get("scheduled_slot")},
        )
        return success(result, "Booking created successfully")

    async def manager_create(self, current_user: CurrentUser, payload: ManagerBookingCreateRequest):
        result = await self.service.create_booking_for_customer(current_user.id, payload)
        await self.audit.log_action(current_user.id, current_user.role, "MANAGER_CREATE_BOOKING", "bookings", result["id"], {"customer_id": payload.customer_id})
        return success(result, "Booking created successfully")

    async def report_risk(self, current_user: CurrentUser, booking_id: str, payload: ReportRiskRequest):
        result = await self.service.report_risk(booking_id, current_user.id, payload.note)
        return success(result, "Manager notified")

    async def eligible_captains(self, current_user: CurrentUser, db: AsyncIOMotorDatabase, booking_id: str):
        return success(
            await StaffDirectoryService(db).eligible_captains_for_booking(
                booking_id, db, current_user.role, current_user.service_center_id
            )
        )

    async def get(self, current_user: CurrentUser, booking_id: str):
        return success(await self.service.get_booking_with_history(booking_id, current_user.id, current_user.role))

    async def list_my_bookings(self, current_user: CurrentUser, status: str | None, pagination: PaginationParams):
        items, total = await self.service.list_for_customer(current_user.id, status, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_my_jobs(self, current_user: CurrentUser, status: str | None, pagination: PaginationParams):
        items, total = await self.service.list_for_captain(current_user.id, status, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_for_center(self, current_user: CurrentUser, service_center_id: str, status: str | None, pagination: PaginationParams):
        items, total = await self.service.list_for_center(
            service_center_id, status, pagination.page, pagination.page_size, current_user.role, current_user.service_center_id
        )
        return paginated(items, pagination.page, pagination.page_size, total)

    async def list_subscribers_for_center(self, current_user: CurrentUser, service_center_id: str):
        return success(await self.service.list_subscribers_for_center(service_center_id, current_user.role, current_user.service_center_id))

    async def list_all(self, filters: dict, pagination: PaginationParams):
        items, total = await self.service.list_all(filters, pagination.page, pagination.page_size)
        return paginated(items, pagination.page, pagination.page_size, total)

    async def assign_captain(self, current_user: CurrentUser, booking_id: str, payload: BookingAssignCaptainRequest):
        result = await self.service.assign_captain(booking_id, payload, current_user.id, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "ASSIGN_CAPTAIN", "bookings", booking_id, {"captain_id": payload.captain_id})
        return success(result, "Captain assigned successfully")

    async def reassign_captain(self, current_user: CurrentUser, booking_id: str, payload: ReassignCaptainRequest):
        result = await self.service.reassign_captain(booking_id, payload, current_user.id, current_user.role, current_user.service_center_id)
        await self.audit.log_action(current_user.id, current_user.role, "REASSIGN_CAPTAIN", "bookings", booking_id, {"captain_id": payload.captain_id})
        return success(result, "Booking reassigned successfully")

    async def captain_cancel(self, current_user: CurrentUser, booking_id: str, payload: CaptainCancelRequest):
        result = await self.service.captain_cancel(booking_id, payload, current_user.id)
        return success(result, "Booking released back to the queue")

    async def start_heading(self, current_user: CurrentUser, booking_id: str, payload: HeadingRequest):
        result = await self.service.start_heading(booking_id, payload, current_user.id)
        return success(result, "Heading to customer")

    async def verify_vehicle(self, current_user: CurrentUser, booking_id: str, payload: VerifyVehicleRequest):
        result = await self.service.verify_vehicle(booking_id, payload, current_user.id)
        return success(result, "Vehicle verified")

    async def resolve_issue(self, current_user: CurrentUser, booking_id: str, payload: ResolveIssueRequest):
        result = await self.service.resolve_issue(booking_id, current_user.id, payload.note, current_user.role, current_user.service_center_id)
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
        return success(result, "Service started")

    async def capture_after_photo(self, current_user: CurrentUser, booking_id: str, payload: PhotoCaptureRequest):
        result = await self.service.capture_after_photo_and_complete(booking_id, payload, current_user.id)
        return success(result, "Service completed")

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

    async def rebook(self, current_user: CurrentUser, booking_id: str, scheduled_date: datetime, scheduled_slot: str):
        result = await self.service.rebook(current_user.id, booking_id, scheduled_date, scheduled_slot)
        return success(result, "Booking created from previous service")
