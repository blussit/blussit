"""
Google sign-in via the Identity Services ID-token flow.

The browser's "Continue with Google" button hands us a signed ID token
(JWT). The ONLY thing we trust is Google's own verification of it —
POSTed to Google's tokeninfo endpoint, then checked for our client id
(aud) and a verified email. No client secret is involved in this flow.

Account rules:
  - match by google_sub first, then by verified email (links an existing
    email+password account to Google on first Google login);
  - new users are created as customers with NO phone — the booking gate
    (phone_verification_fresh) forces adding + verifying a phone before
    the first booking, since the phone is BLUSSIT's primary contact;
  - no password gate for Google users (they sign in with Google;
    must_change_password stays False, a password can be set later via
    forgot-password if they ever want one).
"""
import logging
from datetime import datetime, timezone
import random
import string

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnauthorizedException
from app.core.security import hash_password
from app.models.enums import UserRole, UserStatus
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


class GoogleAuthService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.auth = AuthService(db)

    @staticmethod
    def public_config() -> dict:
        return {"enabled": bool(settings.GOOGLE_OAUTH_CLIENT_ID), "client_id": settings.GOOGLE_OAUTH_CLIENT_ID or None}

    async def _verify_id_token(self, credential: str) -> dict:
        if not settings.GOOGLE_OAUTH_CLIENT_ID:
            raise BadRequestException("Google sign-in is not configured")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(TOKENINFO_URL, params={"id_token": credential})
        except httpx.HTTPError as exc:
            logger.error("Google tokeninfo raised %s", exc)
            raise UnauthorizedException("Could not verify Google sign-in — try again") from exc
        if response.status_code >= 300:
            raise UnauthorizedException("Google sign-in could not be verified")
        info = response.json()
        if info.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID:
            logger.warning("Google token for wrong audience: %s", info.get("aud"))
            raise UnauthorizedException("Google sign-in could not be verified")
        if info.get("email_verified") not in (True, "true"):
            raise UnauthorizedException("Your Google email is not verified")
        return info

    async def login_with_google(self, credential: str) -> dict:
        info = await self._verify_id_token(credential)
        sub, email = info["sub"], info.get("email", "").lower()
        name = info.get("name") or email.split("@")[0]
        picture = info.get("picture")

        user = await self.db.users.find_one({"google_sub": sub, "is_deleted": {"$ne": True}})
        if not user and email:
            user = await self.db.users.find_one({"email": email, "is_deleted": {"$ne": True}})
            if user:
                # First Google login on an existing email account — link it.
                await self.db.users.update_one({"_id": user["_id"]}, {"$set": {"google_sub": sub}})

        if user:
            if user.get("status") == UserStatus.SUSPENDED.value:
                raise UnauthorizedException("Your account has been suspended. Contact support.")
            await self.auth.users.update_by_id(str(user["_id"]), {"last_login_at": datetime.now(timezone.utc)})
            fresh = await self.auth.users.find_by_id(str(user["_id"]))
            return self.auth._issue_tokens(fresh)

        # New customer — created WITHOUT a phone; the booking gate collects
        # and verifies one before their first booking.
        password = "".join(random.choices(string.ascii_letters + string.digits, k=24))
        # Absent email must OMIT the key entirely (never store null) — the
        # sparse-unique email index treats two explicit nulls as duplicates,
        # turning the second no-email Google account into a 500.
        doc: dict = {
            "full_name": name,
            "password_hash": hash_password(password),
            "role": UserRole.CUSTOMER.value,
            "status": UserStatus.ACTIVE.value,
            "google_sub": sub,
            "auth_provider": "google",
            "profile_image": picture,
            "referral_code": self.auth._generate_referral_code(name),
            "phone_verified": False,
        }
        if email:
            doc["email"] = email
        created = await self.auth.users.create(doc)
        return self.auth._issue_tokens(created)
