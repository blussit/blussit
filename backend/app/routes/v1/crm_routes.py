from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.dependencies import CurrentUser, get_current_user, get_db, require_manager_or_admin
from app.core.responses import success
from app.services.crm_service import CRMService

router = APIRouter(prefix="/crm", tags=["CRM"], dependencies=[Depends(require_manager_or_admin)])


# Must be declared before /customers/{customer_id} — Starlette matches routes
# in declaration order, so a literal /customers/search path has to come
# first or it gets swallowed by the {customer_id} path param.
@router.get("/customers/search")
async def search_customer_by_phone(phone: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Used by the manager 'book on behalf of a customer' flow to find an
    existing customer by phone before falling back to creating a new one."""
    return success(await CRMService(db).find_customer_by_phone(phone))


@router.get("/customers/typeahead")
async def search_customers_typeahead(q: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Live suggestions as a manager types a name or number into a booking
    or plan-sale form, so an existing customer is picked directly instead
    of a duplicate account getting created."""
    return success(await CRMService(db).search_customers(q))


@router.get("/customers/{customer_id}")
async def get_customer_360(customer_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """A manager only ever sees this customer's activity at THEIR OWN
    center (see CRMService.get_customer_360's own comment) — admin is
    unrestricted."""
    return success(await CRMService(db).get_customer_360(customer_id, current_user.role, current_user.service_center_id))
