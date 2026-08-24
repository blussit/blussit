# BLUSSIT — Pre-Launch Website

Doorstep vehicle-care pre-launch site for the Indore launch (14 September
2026 · 10:00 AM). Pure frontend — React + Vite + TypeScript + Tailwind —
with no backend to host. The lead form submits straight to
[Web3Forms](https://web3forms.com), which emails every submission to
your mailbox.

```
frontend/   React + Vite + TypeScript + Tailwind (the entire site)
```

## 1. Get a Web3Forms access key (free, ~30 seconds)

1. Go to https://web3forms.com
2. Enter the email address you want lead notifications delivered to.
3. Web3Forms emails you an **Access Key** instantly — no signup/backend
   needed, no card required on the free tier.

## 2. Configure and run

```bash
cd frontend
cp .env.example .env
```

Open `.env` and paste your key:

```
VITE_WEB3FORMS_ACCESS_KEY=your-access-key-here
```

Install and run:

```bash
npm install
npm run dev
```

Opens on `http://localhost:5173`. Submit the pre-launch form and you'll
receive an email (to the address you registered with Web3Forms)
containing the visitor's name, mobile, area, vehicle type, and
interested services.

### Production build

```bash
npm run build
```

Outputs static files to `frontend/dist/` — deploy that folder to any
static host (Vercel, Netlify, Cloudflare Pages, GitHub Pages). Set the
same `VITE_WEB3FORMS_ACCESS_KEY` as an environment variable on your
hosting provider (not just in a local `.env`, which isn't committed).

## 3. What Web3Forms handles for you

- Delivers each submission as an email to your inbox — no server to run.
- Built-in spam filtering (honeypot field already wired up in the code)
  and basic rate limiting on their side.
- Free tier supports a generous number of submissions/month — check
  https://web3forms.com/pricing if you expect very high volume.

If you'd rather see leads in a dashboard, list, or CRM instead of only
email, Web3Forms also has a paid tier with a submissions dashboard — or
you can later add a small backend (e.g. Node/Express) that both stores
leads and forwards to Brevo/Web3Forms; the current `submitLead()` call
in `src/services/api/leadsApi.ts` is the only place that would need to
change.

## 4. Brand assets

`public/blussit-logo.png` (wordmark) and `public/blussit-mark.png` (the
"B" monogram) are the real BLUSSIT logo files, extracted from the brand
reference and saved with a transparent background — no placeholder
swap needed. `favicon.png` / `icon-180.png` / `icon-192.png` /
`icon-512.png` are generated from the monogram for the browser tab and
home-screen icon.

The hero's visual panel is an abstract dark/gold pattern card (no
photography required) with the monogram and tagline overlaid. If you
later get real photography — a BLUSSIT professional cleaning a car at
an Indian residential doorstep — you can swap the pattern `<div>` in
`src/components/sections/Hero.tsx` for an `<img>`.

Also add a real `public/og-image.jpg` (1200×630) referenced in
`index.html` for social-share previews.

## 5. Editing launch details

Everything launch-related — city, date, time, headline, CTA text — lives
in one file: `src/config/site.ts`. Change it there; every section (hero
+ countdown, footer) reads from it. The countdown
(`src/hooks/useCountdown.ts`) is timezone-aware (Asia/Kolkata) and
automatically switches to "WE ARE LIVE" after the launch moment
passes — nothing to update manually on launch day.

The page is intentionally a single, short scroll — Navigation → Hero
(headline + live countdown + visual) → Why BLUSSIT (4 feature cards) →
Lead form → Footer — matching the BLUSSIT brand reference. Add sections
back in `src/App.tsx` only if the page genuinely needs them; longer
pre-launch pages tend to lose visitors before the lead form.

## 6. What's intentionally not included

Per the brief, this stays lightweight: no backend, no database, no
Redis, no WebSockets, no payments, no login, and no fabricated
testimonials, stats, or partner logos.
