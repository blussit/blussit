from datetime import timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.repositories.booking_repository import BookingRepository
from app.repositories.service_center_repository import ServiceCenterRepository
from app.repositories.subscription_repository import UserSubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.utils.timezone import now_ist


class AnalyticsService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.booking_repo = BookingRepository(db)
        self.user_repo = UserRepository(db)
        self.center_repo = ServiceCenterRepository(db)
        self.subscription_repo = UserSubscriptionRepository(db)

    async def dashboard_summary(self) -> dict:
        now = now_ist()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total_revenue_pipeline = [
            {"$match": {"status": "completed", "is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}},
        ]
        total_revenue = await self.booking_repo.aggregate(total_revenue_pipeline)
        total_revenue_val = total_revenue[0]["total"] if total_revenue else 0

        today_orders = await self.booking_repo.count({"created_at": {"$gte": today_start}})
        monthly_orders = await self.booking_repo.count({"created_at": {"$gte": month_start}})
        active_customers = await self.user_repo.count({"role": "customer", "status": "active"})
        cancelled = await self.booking_repo.count({"status": "cancelled"})
        completed = await self.booking_repo.count({"status": "completed"})
        total_bookings = await self.booking_repo.count({})

        subscription_revenue_pipeline = [
            {"$match": {"payment_method": "subscription", "status": "completed", "is_deleted": {"$ne": True}}},
            {"$group": {"_id": None, "total": {"$sum": "$subtotal"}}},
        ]
        subscription_revenue = await self.booking_repo.aggregate(subscription_revenue_pipeline)
        subscription_revenue_val = subscription_revenue[0]["total"] if subscription_revenue else 0

        repeat_customers_pipeline = [
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$customer_id", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        repeat_customers = await self.booking_repo.aggregate(repeat_customers_pipeline)
        total_customers_with_bookings_pipeline = [
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$customer_id"}},
        ]
        distinct_customers = await self.booking_repo.aggregate(total_customers_with_bookings_pipeline)
        repeat_rate = (len(repeat_customers) / len(distinct_customers) * 100) if distinct_customers else 0

        top_services_pipeline = [
            {"$match": {"is_deleted": {"$ne": True}}},
            {"$unwind": "$service_ids"},
            {"$group": {"_id": "$service_ids", "bookings": {"$sum": 1}}},
            {"$sort": {"bookings": -1}},
            {"$limit": 5},
        ]
        top_services = await self.booking_repo.aggregate(top_services_pipeline)

        best_captains_pipeline = [
            {"$match": {"status": "completed", "captain_id": {"$ne": None}, "is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$captain_id", "jobs_completed": {"$sum": 1}, "revenue": {"$sum": "$total_amount"}}},
            {"$sort": {"jobs_completed": -1}},
            {"$limit": 5},
        ]
        best_captains = await self.booking_repo.aggregate(best_captains_pipeline)

        best_centers_pipeline = [
            {"$match": {"status": "completed", "is_deleted": {"$ne": True}}},
            {"$group": {"_id": "$service_center_id", "jobs_completed": {"$sum": 1}, "revenue": {"$sum": "$total_amount"}}},
            {"$sort": {"revenue": -1}},
            {"$limit": 5},
        ]
        best_centers = await self.booking_repo.aggregate(best_centers_pipeline)

        return {
            "total_revenue": round(total_revenue_val, 2),
            "todays_orders": today_orders,
            "monthly_orders": monthly_orders,
            "active_customers": active_customers,
            "repeat_customer_rate": round(repeat_rate, 2),
            "subscription_revenue": round(subscription_revenue_val, 2),
            "top_services": [{"service_id": s["_id"], "bookings": s["bookings"]} for s in top_services],
            "best_performing_captains": [{"captain_id": c["_id"], "jobs_completed": c["jobs_completed"], "revenue": round(c["revenue"], 2)} for c in best_captains],
            "best_service_centers": [{"service_center_id": c["_id"], "jobs_completed": c["jobs_completed"], "revenue": round(c["revenue"], 2)} for c in best_centers],
            "cancellation_rate": round((cancelled / total_bookings * 100), 2) if total_bookings else 0,
            "completion_rate": round((completed / total_bookings * 100), 2) if total_bookings else 0,
            "total_bookings": total_bookings,
        }

    async def booking_trends(self, days: int = 30) -> list[dict]:
        start = now_ist() - timedelta(days=days)
        pipeline = [
            {"$match": {"created_at": {"$gte": start}, "is_deleted": {"$ne": True}}},
            {
                "$group": {
                    "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at", "timezone": "+05:30"}},
                    "bookings": {"$sum": 1},
                    "revenue": {"$sum": "$total_amount"},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        results = await self.booking_repo.aggregate(pipeline)
        return [{"date": r["_id"], "bookings": r["bookings"], "revenue": round(r["revenue"], 2)} for r in results]
