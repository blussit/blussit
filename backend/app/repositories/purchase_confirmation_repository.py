from app.repositories.base_repository import BaseRepository


class PurchaseConfirmationRepository(BaseRepository):
    collection_name = "purchase_confirmations"

    async def find_by_token(self, token: str):
        return await self.find_one({"token": token})
