# Doorstep Vehicle Care Platform — Frontend

React 19 + Vite + TypeScript + Tailwind CSS v4 client covering the public
website and all four portals (Customer, Captain, Manager, Super Admin).

## Setup

```bash
cd frontend
npm install
cp .env.example .env   # set VITE_API_BASE_URL if the backend isn't on localhost:8000
npm run dev
```

The app expects the backend running at `http://localhost:8000/api/v1` by
default (see `backend/README.md`).

## Structure

- `src/pages/public` — landing page and its sections
- `src/pages/auth` — login, registration, forgot/reset password
- `src/pages/customer` — customer dashboard, booking wizard, vehicles, addresses, subscriptions, support
- `src/pages/captain` — today's jobs, attendance & leave, earnings/performance
- `src/pages/manager` — service center dashboard, booking queue & captain assignment, captains, inventory, complaints
- `src/pages/admin` — analytics dashboard, users, service centers, services/categories, subscription plans, coupons, complaints, audit logs
- `src/pages/shared` — profile & notifications pages reused across all roles
- `src/components/ui` — design-system primitives (Button, Input, Card, Modal, DataTable, Badge, etc.)
- `src/components/layout` — public navbar/footer and the role-aware `DashboardShell`
- `src/api` — typed API client functions grouped by domain
- `src/context/AuthContext.tsx` — auth state, JWT storage, login/register/logout
- `src/lib/api-client.ts` — Axios instance with automatic access-token refresh

## Design system

Colors, typography, and spacing follow the platform's design tokens defined
in `src/index.css` (Tailwind v4 `@theme`): deep blue primary, sky blue
secondary, emerald accent, on a white/light-gray surface. Headings use
Sora, body text uses Inter, and numeric data (prices, booking codes, KPIs)
uses JetBrains Mono for clear tabular alignment.

## Build

```bash
npm run build
```

Outputs to `dist/`. Type-checking (`tsc -b`) runs as part of the build.
