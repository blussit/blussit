# BLUSSIT — Go-Live Checklist

Last updated: 2026-09-03

## Already done (code side, verified)

- Full pytest suite green; frontend production build clean.
- JWT secret replaced with a random 64-char key (dev `.env`); generate a DIFFERENT one for production.
- Login brute-force lockout: 5 wrong passwords = 5-minute lock (tested).
- API docs (`/api/docs`) hidden automatically when `DEBUG=false`.
- Server never leaks stack traces; health check at `/api/health`.
- Landing hero image compressed 1.9MB → 211KB.
- WhatsApp notifications live via approved utility template; webhook handshake + signature check ready; test suite can never send real messages.
- OTP delivery: WhatsApp first with SMS fallback (SMS activates when a key is added).

## YOUR STEPS — in order

### 1. Deploy the backend (the big one)
Everything currently runs on your laptop. Pick a host (Railway / Render / a small VPS), deploy `backend/`, point `api.blussit.com` at it. Production env vars to set there:
- `APP_ENV=production`, `DEBUG=false`
- `JWT_SECRET_KEY=` (fresh random value — never reuse dev's)
- `MONGO_URI=` / `MONGO_DB_NAME=` (consider a separate production DB name; Atlas Network Access must allow the host's IPs)
- `CORS_ORIGINS=https://blussit.com,https://www.blussit.com`
- All `WHATSAPP_*` values from dev `.env`, PLUS `WHATSAPP_APP_SECRET` (Meta App Dashboard → Settings → Basic → App secret)

### 2. Point the frontend at it
Vercel project → Environment Variables → `VITE_API_BASE_URL=https://api.blussit.com/api/v1` → redeploy.

### 3. Meta / WhatsApp (all in Business/App Dashboard)
- **Permanent token**: Business Settings → Users → System users → create system user → generate token with `whatsapp_business_messaging` + `whatsapp_business_management` → replace `WHATSAPP_ACCESS_TOKEN` (current temp token EXPIRES ~24h!).
- **Webhook**: Callback URL `https://api.blussit.com/api/v1/whatsapp/webhook`, verify token `blussit-wa-verify-8k3n9x`, subscribe to `messages`.
- **App Mode → Live** (needs a Privacy Policy URL in Settings → Basic). Unlocks messaging ANY number.
- **Business Verification** (needs Udyam registration): unlocks the OTP + temp-password templates and lifts the 250-conversations/day cap.
- Templates: generic carrier is now `blussit_service_update` (Utility, approved) — Blussit-branded, done. 8 event templates approved as Utility and live in automation. `blussit_welcome`/`blussit_notify`/`blussit_update` got forced into Marketing — kept on the WABA but disabled in our system (never sent at marketing price). `blussit_account_created` (Utility, the welcome replacement) is pending review — one Sync in Admin → WhatsApp → Templates activates it once Active.

### 4. Content
- Privacy Policy page on blussit.com (required for Live mode).
- Add testimonials via admin (landing section hides until some exist).
- Final images for the landing page.

### 5. Optional but recommended
- Fast2SMS API key → `SMS_PROVIDER=fast2sms`, `FAST2SMS_API_KEY=...` (OTP fallback channel).
- UptimeRobot (free) pinging `https://api.blussit.com/api/health` — alerts you if the API goes down.

### 6. Google (Maps + Sign-in) — before real customers
- The Maps browser key is currently **IP-restricted to this machine** — works in dev only. Switch it to **Website restriction** (`blussit.com/*`, `www.blussit.com/*`) in Google Cloud console before launch, and keep a separate IP-restricted key for the server (Routes API).
- Enable all four APIs on the project: Maps JavaScript, Places, Geocoding, **Routes**.
- Google OAuth client: add `https://blussit.com` to **Authorized JavaScript origins** (localhost already works).
- Keep the ₹500 budget alert + per-API daily quota caps.

## Ad tracking
Conversion URL for Meta/Google Ads: `https://blussit.com/thank-you` (reachable only after a real booking/purchase).
