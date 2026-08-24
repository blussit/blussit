from app.repositories.base_repository import BaseRepository


class CaptainWalletRepository(BaseRepository):
    collection_name = "captain_wallets"

    async def find_by_captain(self, captain_id: str) -> dict | None:
        return await self.find_one({"captain_id": captain_id})


class WalletTransactionRepository(BaseRepository):
    collection_name = "wallet_transactions"

    async def list_for_captain(self, captain_id: str, page: int, page_size: int):
        return await self.find_many({"captain_id": captain_id}, page=page, page_size=page_size)


class WithdrawalRequestRepository(BaseRepository):
    collection_name = "withdrawal_requests"

    async def list_for_captain(self, captain_id: str, page: int, page_size: int):
        return await self.find_many({"captain_id": captain_id}, page=page, page_size=page_size)

    async def list_pending(self, page: int, page_size: int):
        return await self.find_many({"status": "pending"}, page=page, page_size=page_size)
