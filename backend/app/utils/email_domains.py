"""
Consumer email-domain allow-list.

Founder call: customer sign-ups accept only mainstream consumer mail
providers (gmail, yahoo, outlook/hotmail, icloud, rediffmail, proton,
zoho, aol) — not arbitrary domains. This blocks the throwaway
"@test.xyz" addresses that make a customer record unusable for
receipts and password recovery.

Scope note: this is applied to CUSTOMER-facing sign-up paths only.
Staff accounts (captain/manager/admin) deliberately keep full email
freedom because company domains are legitimate there, and Google
sign-in is exempt because Google has already verified the address.
"""

ALLOWED_EMAIL_DOMAINS = frozenset({
    # Google
    "gmail.com", "googlemail.com",
    # Yahoo
    "yahoo.com", "yahoo.in", "yahoo.co.in", "ymail.com", "rocketmail.com",
    # Microsoft
    "outlook.com", "outlook.in", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com",
    # Apple
    "icloud.com", "me.com", "mac.com",
    # India / other mainstream
    "rediffmail.com", "zoho.com", "zohomail.in", "protonmail.com", "proton.me", "aol.com",
})

_ALLOWED_LABEL = "Gmail, Yahoo, Outlook/Hotmail, iCloud, Rediffmail, Proton or Zoho"


def is_allowed_email_domain(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""
    return domain in ALLOWED_EMAIL_DOMAINS


def validate_consumer_email(email: str | None) -> str | None:
    """Normalises and enforces the allow-list. Returns the lower-cased
    address, or raises ValueError with a message the customer can act on."""
    if not email:
        return email
    cleaned = email.strip().lower()
    if not is_allowed_email_domain(cleaned):
        raise ValueError(f"Please use a {_ALLOWED_LABEL} email address.")
    return cleaned
