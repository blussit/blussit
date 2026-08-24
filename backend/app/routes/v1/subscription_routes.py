from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.controllers.subscription_controller import SubscriptionPlanController, UserSubscriptionController
from app.core.dependencies import CurrentUser, PaginationParams, get_current_user, get_db, require_admin, require_customer, require_manager_or_admin
from app.schemas.subscription_schema import (
    AssignSubscriptionRequest,
    SubscribeRequest,
    SubscriptionPlanCreateRequest,
    SubscriptionPlanUpdateRequest,
    UpgradeSubscriptionRequest,
)

plan_router = APIRouter(prefix="/subscription-plans", tags=["Subscription Plans"])
subscription_router = APIRouter(prefix="/subscriptions", tags=["User Subscriptions"])


@plan_router.get("")
async def list_plans(active_only: bool = True, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await SubscriptionPlanController(db).list(active_only)


@plan_router.get("/{plan_id}")
async def get_plan(plan_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    return await SubscriptionPlanController(db).get(plan_id)


@plan_router.post("", dependencies=[Depends(require_admin)])
async def create_plan(payload: SubscriptionPlanCreateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await SubscriptionPlanController(db).create(current_user, payload)


@plan_router.put("/{plan_id}", dependencies=[Depends(require_admin)])
async def update_plan(plan_id: str, payload: SubscriptionPlanUpdateRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await SubscriptionPlanController(db).update(current_user, plan_id, payload)


@plan_router.delete("/{plan_id}", dependencies=[Depends(require_admin)])
async def delete_plan(plan_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await SubscriptionPlanController(db).delete(current_user, plan_id)


@subscription_router.get("/my", dependencies=[Depends(require_customer)])
async def list_my_subscriptions(current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await UserSubscriptionController(db).list_mine(current_user)


@subscription_router.get("/customer/{customer_id}", dependencies=[Depends(require_manager_or_admin)])
async def list_customer_subscriptions(customer_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Manager/admin equivalent of GET /subscriptions/my for an arbitrary
    customer — powers the manager booking flow's subscription picker and
    the assign-a-plan action."""
    return await UserSubscriptionController(db).list_for_customer(customer_id)


@subscription_router.post("", dependencies=[Depends(require_customer)])
async def subscribe(payload: SubscribeRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await UserSubscriptionController(db).subscribe(current_user, payload)


@subscription_router.post("/assign", dependencies=[Depends(require_manager_or_admin)])
async def assign_subscription(payload: AssignSubscriptionRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    """Manager/admin grants a subscription to a customer directly — same
    validation as self-purchase (vehicle ownership, vehicle-type match,
    one-active-subscription-per-vehicle)."""
    return await UserSubscriptionController(db).assign(current_user, payload)


@subscription_router.post("/{subscription_id}/cancel", dependencies=[Depends(require_customer)])
async def cancel_subscription(subscription_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await UserSubscriptionController(db).cancel(current_user, subscription_id)


@subscription_router.post("/{subscription_id}/upgrade", dependencies=[Depends(require_customer)])
async def upgrade_subscription(subscription_id: str, payload: UpgradeSubscriptionRequest, current_user: CurrentUser = Depends(get_current_user), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await UserSubscriptionController(db).upgrade(current_user, subscription_id, payload)


@subscription_router.get("/admin/all", dependencies=[Depends(require_admin)])
async def list_all_subscriptions(pagination: PaginationParams = Depends(), db: AsyncIOMotorDatabase = Depends(get_db)):
    return await UserSubscriptionController(db).list_all_for_admin(pagination)
