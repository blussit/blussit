import { lazy, Suspense } from "react";
import { PageLoader } from "./components/ui";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";
import { MutationCache, QueryClient, QueryClientProvider, hydrate, type DehydratedState } from "@tanstack/react-query";
import { getErrorMessage } from "./lib/api-client";
import { toastBus } from "./context/ToastContext";
import { AuthProvider } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ToastContainer } from "./components/shared/ToastContainer";
import { ConfirmProvider } from "./context/ConfirmContext";
import { ConfirmDialog } from "./components/shared/ConfirmDialog";
import { ProtectedRoute, GuestOnlyRoute } from "./routes/ProtectedRoute";
import { StaffBookingRedirect } from "./routes/StaffBookingRedirect";
import { ScrollRestoration } from "./components/shared/ScrollRestoration";
import { VisitBeacon } from "./components/shared/VisitBeacon";

import LandingPage from "./pages/public/LandingPage";
// The search-landing pages are pre-rendered to HTML at build time, so they
// load with the entry bundle: a lazy chunk would swap that HTML for a
// spinner before showing the very same page.
import ServicesPage from "./pages/public/seo/ServicesPage";
import ServiceDetailPage from "./pages/public/seo/ServiceDetailPage";
import IndoreCarWashPage from "./pages/public/seo/IndoreCarWashPage";
import PlansPage from "./pages/public/seo/PlansPage";
import NotFoundPage from "./pages/public/seo/NotFoundPage";
import ServicePolicyPage from "./pages/public/ServicePolicyPage";
import CancellationPolicyPage from "./pages/public/CancellationPolicyPage";
import PrivacyPolicyPage from "./pages/public/PrivacyPolicyPage";
import TermsPage from "./pages/public/TermsPage";
import { loadBookPage, loadLoginPage } from "./routes/prefetch";

// Only the landing page ships in the first download; everything else loads
// on demand (the booking wizard and login are also prefetched while idle —
// see routes/prefetch.ts — so they still open instantly).
const BookPage = lazy(loadBookPage);
const LoginPage = lazy(loadLoginPage);
// Local test setup only — dropped entirely from production builds.
const LocalEnvBadge = import.meta.env.DEV ? lazy(() => import("./components/dev/LocalEnvBadge")) : null;
const ForgotPasswordPage = lazy(() => import("./pages/auth/ForgotPasswordPage"));
const SocietyFormPage = lazy(() => import("./pages/society/SocietyFormPage"));

const CustomerLayout = lazy(() => import("./pages/customer/CustomerLayout"));
const DashboardHomePage = lazy(() => import("./pages/customer/DashboardHomePage"));
const NewBookingPage = lazy(() => import("./pages/customer/NewBookingPage"));
const MyBookingsPage = lazy(() => import("./pages/customer/MyBookingsPage"));
const BookingDetailPage = lazy(() => import("./pages/customer/BookingDetailPage"));
const ThankYouPage = lazy(() => import("./pages/customer/ThankYouPage"));
const SubscriptionsPage = lazy(() => import("./pages/customer/SubscriptionsPage"));
const AddressesPage = lazy(() => import("./pages/customer/AddressesPage"));
const SupportPage = lazy(() => import("./pages/customer/SupportPage"));
const GaragePage = lazy(() => import("./pages/customer/GaragePage"));
const SettingsPage = lazy(() => import("./pages/customer/SettingsPage"));
const OffersPage = lazy(() => import("./pages/customer/OffersPage"));
const WalletPage = lazy(() => import("./pages/customer/WalletPage"));

const CaptainLayout = lazy(() => import("./pages/captain/CaptainLayout"));
const CaptainTodayPage = lazy(() => import("./pages/captain/CaptainTodayPage"));
const CaptainJobsPage = lazy(() => import("./pages/captain/CaptainJobsPage"));
const CaptainJobPage = lazy(() => import("./pages/captain/CaptainJobPage"));
const CaptainAttendancePage = lazy(() => import("./pages/captain/CaptainAttendancePage"));
const CaptainEarningsPage = lazy(() => import("./pages/captain/CaptainEarningsPage"));
const CaptainProfilePage = lazy(() => import("./pages/captain/CaptainProfilePage"));
const CaptainSocietiesPage = lazy(() => import("./pages/captain/CaptainSocietiesPage"));

const ManagerLayout = lazy(() => import("./pages/manager/ManagerLayout"));
const ManagerDashboardPage = lazy(() => import("./pages/manager/ManagerDashboardPage"));
const ManagerNewBookingPage = lazy(() => import("./pages/manager/ManagerNewBookingPage"));
const ManagerLogJobPage = lazy(() => import("./pages/manager/ManagerLogJobPage"));
const BookingQueuePage = lazy(() => import("./pages/manager/BookingQueuePage"));
const ManagerCaptainsPage = lazy(() => import("./pages/manager/ManagerCaptainsPage"));
const ManagerSubscribersPage = lazy(() => import("./pages/manager/ManagerSubscribersPage"));
const ManagerSellPlanPage = lazy(() => import("./pages/manager/ManagerSellPlanPage"));
const ManagerInventoryPage = lazy(() => import("./pages/manager/ManagerInventoryPage"));
const ManagerComplaintsPage = lazy(() => import("./pages/manager/ManagerComplaintsPage"));
const ManagerReviewsPage = lazy(() => import("./pages/manager/ManagerReviewsPage"));
const ManagerSocietiesPage = lazy(() => import("./pages/manager/ManagerSocietiesPage"));
const ManagerSocietyDetailPage = lazy(() => import("./pages/manager/ManagerSocietyDetailPage"));
const ManagerSocietyPlannerPage = lazy(() => import("./pages/manager/ManagerSocietyPlannerPage"));
const ManagerChargesPage = lazy(() => import("./pages/manager/ManagerChargesPage"));
const ManagerCustomPlansPage = lazy(() => import("./pages/manager/ManagerCustomPlansPage"));

const AdminLayout = lazy(() => import("./pages/admin/AdminLayout"));
const AdminDashboardPage = lazy(() => import("./pages/admin/AdminDashboardPage"));
const AdminBookingsPage = lazy(() => import("./pages/admin/AdminBookingsPage"));
const AdminUsersPage = lazy(() => import("./pages/admin/AdminUsersPage"));
const AdminServiceCentersPage = lazy(() => import("./pages/admin/AdminServiceCentersPage"));
const AdminServiceZonesPage = lazy(() => import("./pages/admin/AdminServiceZonesPage"));
const AdminSlotCapacityPage = lazy(() => import("./pages/admin/AdminSlotCapacityPage"));
const AdminServicesPage = lazy(() => import("./pages/admin/AdminServicesPage"));
const AdminVehicleTypesPage = lazy(() => import("./pages/admin/AdminVehicleTypesPage"));
const AdminComboOffersPage = lazy(() => import("./pages/admin/AdminComboOffersPage"));
const AdminHomepageSettingsPage = lazy(() => import("./pages/admin/AdminHomepageSettingsPage"));
const AdminContactMessagesPage = lazy(() => import("./pages/admin/AdminContactMessagesPage"));
const AdminPlanEnquiriesPage = lazy(() => import("./pages/admin/AdminPlanEnquiriesPage"));
const AdminWhatsAppPage = lazy(() => import("./pages/admin/AdminWhatsAppPage"));
const AdminCoverageLeadsPage = lazy(() => import("./pages/admin/AdminCoverageLeadsPage"));
const AdminPricingPage = lazy(() => import("./pages/admin/AdminPricingPage"));
const AdminSubscriptionPlansPage = lazy(() => import("./pages/admin/AdminSubscriptionPlansPage"));
const AdminPurchasedPlansPage = lazy(() => import("./pages/admin/AdminPurchasedPlansPage"));
const AdminCouponsPage = lazy(() => import("./pages/admin/AdminCouponsPage"));
const AdminComplaintsPage = lazy(() => import("./pages/admin/AdminComplaintsPage"));
const AdminReviewsPage = lazy(() => import("./pages/admin/AdminReviewsPage"));
const AdminAuditLogsPage = lazy(() => import("./pages/admin/AdminAuditLogsPage"));
const AdminSocietiesPage = lazy(() => import("./pages/admin/AdminSocietiesPage"));
const AdminSocietyDetailPage = lazy(() => import("./pages/admin/AdminSocietyDetailPage"));
const AdminSocietyPlansPage = lazy(() => import("./pages/admin/AdminSocietyPlansPage"));
const AdminSocietyPlannerPage = lazy(() => import("./pages/admin/AdminSocietyPlannerPage"));
const AdminChargesPage = lazy(() => import("./pages/admin/AdminChargesPage"));
const AdminCustomPlansPage = lazy(() => import("./pages/admin/AdminCustomPlansPage"));

const ProfilePage = lazy(() => import("./pages/shared/ProfilePage"));
const CustomerProfilePage = lazy(() => import("./pages/customer/CustomerProfilePage"));
const NotificationsPage = lazy(() => import("./pages/shared/NotificationsPage"));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 } },
  mutationCache: new MutationCache({
    // Safety net for every mutation that doesn't handle its own error —
    // roughly a dozen admin/manager actions used to fail in total silence
    // (suspend user, delete coupon, resolve issue, mark-read, ...).
    onError: (error, _variables, _context, mutation) => {
      if (mutation.options.onError) return; // handled locally
      toastBus.emit?.({ tone: "error", title: "That Didn't Save", message: getErrorMessage(error) });
    },
  }),
});

// A pre-rendered page ships the API data it was rendered with (see
// scripts/prerender.mjs): start from it so the first render matches the HTML,
// then refetch as usual — it's stale by definition.
const prerendered = (window as { __BLUSSIT_QUERIES__?: DehydratedState }).__BLUSSIT_QUERIES__;
if (prerendered) hydrate(queryClient, prerendered);

// A render crash anywhere in the route tree used to take the whole app to
// a blank white page — nothing caught it. This resets automatically on
// every navigation (see ErrorBoundary's resetKey), so leaving the page
// that crashed clears it without needing a hard reload.
function RoutedErrorBoundary({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  return <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
        <ToastProvider>
        <ConfirmProvider>
          <ScrollRestoration />
          <VisitBeacon />
          {LocalEnvBadge && (
            <Suspense fallback={null}>
              <LocalEnvBadge />
            </Suspense>
          )}
          <ToastContainer />
          <ConfirmDialog />
          <RoutedErrorBoundary>
          <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            {/* Search-landing pages — keep in step with src/prerender/entry.tsx and src/seo/pages.json. */}
            <Route path="/services" element={<ServicesPage />} />
            <Route path="/services/:slug" element={<ServiceDetailPage />} />
            <Route path="/plans" element={<PlansPage />} />
            <Route path="/doorstep-car-wash-indore" element={<IndoreCarWashPage />} />
            <Route path="/cancellation-policy" element={<CancellationPolicyPage />} />
            <Route path="/service-policy" element={<ServicePolicyPage />} />
            <Route path="/privacy-policy" element={<PrivacyPolicyPage />} />
            <Route path="/terms" element={<TermsPage />} />
            <Route path="/book" element={<BookPage />} />
            {/* Society resident form + hub — public, keyed by the society's link token. */}
            <Route path="/society/:token" element={<SocietyFormPage />} />

            <Route element={<GuestOnlyRoute />}>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            </Route>

            {/* Purchase confirmation — deliberately OUTSIDE the protected
                customer portal. A logged-in customer lands here right
                after a booking/subscription the same as before; a
                not-yet-logged-in guest (once guest checkout exists) needs
                to reach this page too, which ProtectedRoute would
                otherwise block with a redirect to /login before they ever
                see it. The page itself checks auth state to show the
                right actions either way. */}
            <Route path="/thank-you" element={<ThankYouPage />} />

            {/* Customer portal */}
            <Route element={<ProtectedRoute allowedRoles={["customer"]} />}>
              <Route path="/app" element={<CustomerLayout />}>
                <Route index element={<DashboardHomePage />} />
                <Route path="book" element={<NewBookingPage />} />
                <Route path="bookings" element={<MyBookingsPage />} />
                <Route path="bookings/:id" element={<BookingDetailPage />} />
                <Route path="subscriptions" element={<SubscriptionsPage />} />
                <Route path="society/:token" element={<SocietyFormPage embedded />} />
                <Route path="addresses" element={<AddressesPage />} />
                <Route path="support" element={<SupportPage />} />
                <Route path="profile" element={<CustomerProfilePage />} />
                <Route path="garage" element={<GaragePage />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="offers" element={<OffersPage />} />
                <Route path="wallet" element={<WalletPage />} />
                <Route path="notifications" element={<NotificationsPage />} />
              </Route>
            </Route>

            {/* Captain portal */}
            <Route element={<ProtectedRoute allowedRoles={["captain"]} />}>
              <Route path="/captain" element={<CaptainLayout />}>
                <Route index element={<CaptainTodayPage />} />
                <Route path="jobs" element={<CaptainJobsPage />} />
                <Route path="jobs/:id" element={<CaptainJobPage />} />
                <Route path="attendance" element={<CaptainAttendancePage />} />
                <Route path="societies" element={<CaptainSocietiesPage />} />
                <Route path="earnings" element={<CaptainEarningsPage />} />
                <Route path="profile" element={<CaptainProfilePage />} />
                <Route path="notifications" element={<NotificationsPage />} />
              </Route>
            </Route>

            {/* Manager portal */}
            <Route element={<ProtectedRoute allowedRoles={["manager"]} />}>
              <Route path="/manager" element={<ManagerLayout />}>
                <Route index element={<ManagerDashboardPage />} />
                <Route path="kpi" element={<Navigate to="/manager" replace />} />
                <Route path="new-booking" element={<ManagerNewBookingPage />} />
                <Route path="log-job" element={<ManagerLogJobPage />} />
                <Route path="bookings" element={<BookingQueuePage />} />
                {/* Notification deep-links land here — send staff to their
                    real actionable view (the queue, highlighted), not the
                    customer's read-only detail page. */}
                <Route path="bookings/:id" element={<StaffBookingRedirect base="/manager/bookings" />} />
                <Route path="captains" element={<ManagerCaptainsPage />} />
                <Route path="subscribers" element={<ManagerSubscribersPage />} />
                <Route path="sell-plan" element={<ManagerSellPlanPage />} />
                <Route path="custom-plans" element={<ManagerCustomPlansPage />} />
                <Route path="inventory" element={<ManagerInventoryPage />} />
                <Route path="complaints" element={<ManagerComplaintsPage />} />
                <Route path="reviews" element={<ManagerReviewsPage />} />
                <Route path="societies" element={<ManagerSocietiesPage />} />
                <Route path="societies/:id" element={<ManagerSocietyDetailPage />} />
                <Route path="society-planner" element={<ManagerSocietyPlannerPage />} />
                <Route path="charges" element={<ManagerChargesPage />} />
                <Route path="profile" element={<ProfilePage />} />
                <Route path="notifications" element={<NotificationsPage />} />
              </Route>
            </Route>

            {/* Admin portal */}
            <Route element={<ProtectedRoute allowedRoles={["admin"]} />}>
              <Route path="/admin" element={<AdminLayout />}>
                <Route index element={<AdminDashboardPage />} />
                <Route path="bookings" element={<AdminBookingsPage />} />
                <Route path="bookings/:id" element={<StaffBookingRedirect base="/admin/bookings" />} />
                <Route path="users" element={<AdminUsersPage />} />
                <Route path="service-centers" element={<AdminServiceCentersPage />} />
                <Route path="service-zones" element={<AdminServiceZonesPage />} />
                <Route path="service-centers/:centerId/capacity" element={<AdminSlotCapacityPage />} />
                <Route path="services" element={<AdminServicesPage />} />
                <Route path="vehicle-types" element={<AdminVehicleTypesPage />} />
                <Route path="combo-offers" element={<AdminComboOffersPage />} />
                <Route path="homepage" element={<AdminHomepageSettingsPage />} />
                <Route path="whatsapp" element={<AdminWhatsAppPage />} />
                <Route path="contact-messages" element={<AdminContactMessagesPage />} />
                <Route path="plan-enquiries" element={<AdminPlanEnquiriesPage />} />
                <Route path="coverage-requests" element={<AdminCoverageLeadsPage />} />
                <Route path="pricing" element={<AdminPricingPage />} />
                <Route path="subscription-plans" element={<AdminSubscriptionPlansPage />} />
                <Route path="purchased-plans" element={<AdminPurchasedPlansPage />} />
                <Route path="custom-plans" element={<AdminCustomPlansPage />} />
                <Route path="coupons" element={<AdminCouponsPage />} />
                <Route path="complaints" element={<AdminComplaintsPage />} />
                <Route path="reviews" element={<AdminReviewsPage />} />
                <Route path="audit-logs" element={<AdminAuditLogsPage />} />
                <Route path="societies" element={<AdminSocietiesPage />} />
                <Route path="societies/:id" element={<AdminSocietyDetailPage />} />
                <Route path="society-plans" element={<AdminSocietyPlansPage />} />
                <Route path="society-planner" element={<AdminSocietyPlannerPage />} />
                <Route path="charges" element={<AdminChargesPage />} />
                <Route path="profile" element={<ProfilePage />} />
                <Route path="notifications" element={<NotificationsPage />} />
              </Route>
            </Route>

            <Route path="*" element={<NotFoundPage />} />
          </Routes>
          </Suspense>
          </RoutedErrorBoundary>
        </ConfirmProvider>
        </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
