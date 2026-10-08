import { renderToString } from "react-dom/server";
import { Route, Routes, StaticRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider, dehydrate, type DehydratedState } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { AuthProvider } from "../context/AuthContext";
import { ToastProvider } from "../context/ToastContext";
import { ConfirmProvider } from "../context/ConfirmContext";
import { PageSeo } from "../components/shared/PageSeo";
import { bookingPolicyApi, catalogApi, contentApi, vehicleTypeApi } from "../api/catalog";
import { subscriptionApi } from "../api/engagement";
import { societyLeadApi } from "../api/society";
import LandingPage from "../pages/public/LandingPage";
import ServicesPage from "../pages/public/seo/ServicesPage";
import ServiceDetailPage from "../pages/public/seo/ServiceDetailPage";
import IndoreCarWashPage from "../pages/public/seo/IndoreCarWashPage";
import PlansPage from "../pages/public/seo/PlansPage";
import ServicePolicyPage from "../pages/public/ServicePolicyPage";
import CancellationPolicyPage from "../pages/public/CancellationPolicyPage";
import PrivacyPolicyPage from "../pages/public/PrivacyPolicyPage";
import TermsPage from "../pages/public/TermsPage";
import { SERVICES } from "../seo/content";

/**
 * Build-time renderer (`vite build --ssr`, driven by scripts/prerender.mjs).
 * Public pages are rendered to real HTML so search engines — and visitors
 * on slow phones — get the content without waiting for JavaScript.
 *
 * "Full" routes render the page body with the API data they show, fetched
 * with the SAME query keys and functions the components use, so the browser
 * can pick that data up (App.tsx) and its first render matches the HTML.
 * Every other route in pages.json gets its head tags only.
 */

const QUERIES = {
  services: { queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }) },
  vehicleTypes: { queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() },
  plans: { queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true) },
  testimonials: { queryKey: ["public-testimonials"], queryFn: contentApi.testimonials },
  societyPitch: { queryKey: ["society-pitch"], queryFn: societyLeadApi.info },
  // The cancellation charges shown on /cancellation-policy.
  policy: { queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get },
} as const;
type QueryName = keyof typeof QUERIES;

const LANDING_DATA: QueryName[] = ["services", "vehicleTypes", "plans", "testimonials", "societyPitch"];
const SERVICES_DATA: QueryName[] = ["services"];

/** Keep in step with the public routes in App.tsx. */
const FULL_ROUTES: { pattern: RegExp; data: QueryName[] }[] = [
  { pattern: /^\/$/, data: LANDING_DATA },
  { pattern: /^\/services$/, data: SERVICES_DATA },
  { pattern: /^\/services\/[a-z0-9-]+$/, data: SERVICES_DATA },
  { pattern: /^\/doorstep-car-wash-indore$/, data: SERVICES_DATA },
  { pattern: /^\/plans$/, data: ["plans", "services", "vehicleTypes", "societyPitch"] },
  // The policies are trust content people (and Google) read in full.
  { pattern: /^\/service-policy$/, data: SERVICES_DATA },
  { pattern: /^\/cancellation-policy$/, data: ["policy"] },
  { pattern: /^\/(privacy-policy|terms)$/, data: [] },
];

function PublicPages() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/services" element={<ServicesPage />} />
      <Route path="/services/:slug" element={<ServiceDetailPage />} />
      <Route path="/plans" element={<PlansPage />} />
      <Route path="/doorstep-car-wash-indore" element={<IndoreCarWashPage />} />
      <Route path="/service-policy" element={<ServicePolicyPage />} />
      <Route path="/cancellation-policy" element={<CancellationPolicyPage />} />
      <Route path="/privacy-policy" element={<PrivacyPolicyPage />} />
      <Route path="/terms" element={<TermsPage />} />
    </Routes>
  );
}

/** Every /services/:slug page — each must have its own pages.json entry. */
export const SERVICE_PATHS = SERVICES.map((s) => `/services/${s.slug}`);

// One fetch per query for the whole build, however many pages use it.
const fetched = new Map<QueryName, Promise<unknown>>();
function load(name: QueryName): Promise<unknown> {
  if (!fetched.has(name)) {
    const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("timed out")), 15_000));
    fetched.set(name, Promise.race([QUERIES[name].queryFn(), timeout]));
  }
  return fetched.get(name)!;
}

export interface Rendered {
  /** Everything React rendered: head tags first (React 19 hoists them), then the body. */
  html: string;
  /** API data the page was rendered with, for the browser to start from. */
  state: DehydratedState | null;
  /** Data the page wanted but couldn't get — the build logs these. */
  missing: string[];
  full: boolean;
}

export async function render(path: string): Promise<Rendered> {
  const route = FULL_ROUTES.find((r) => r.pattern.test(path));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const missing: string[] = [];
  for (const name of route?.data ?? []) {
    try {
      client.setQueryData(QUERIES[name].queryKey, await load(name));
    } catch (err) {
      missing.push(`${name} (${err instanceof Error ? err.message : String(err)})`);
    }
  }
  const html = renderToString(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <StaticRouter location={path}>
          <AuthProvider>
            <ToastProvider>
              <ConfirmProvider>{route ? <PublicPages /> : <PageSeo path={path} />}</ConfirmProvider>
            </ToastProvider>
          </AuthProvider>
        </StaticRouter>
      </QueryClientProvider>
    </HelmetProvider>
  );
  return { html, state: route?.data.length ? dehydrate(client) : null, missing, full: !!route };
}
