import { lazy, Suspense } from "react";
import { PageLoader } from "./components/ui";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { getErrorMessage } from "./lib/api-client";
import { toastBus } from "./context/ToastContext";
import { AuthProvider } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ToastContainer } from "./components/shared/ToastContainer";
import { ConfirmProvider } from "./context/ConfirmContext";
import { ConfirmDialog } from "./components/shared/ConfirmDialog";
import { ProtectedRoute, GuestOnlyRoute } from "./routes/ProtectedRoute";
import { StaffBookingRedirect } from "./routes/StaffBookingRedirect";

import LandingPage from "./pages/public/LandingPage";
import ServicesPage from "./pages/public/ServicesPage";
import PlansPage from "./pages/public/PlansPage";
import CancellationPolicyPage from "./pages/public/CancellationPolicyPage";
import PrivacyPolicyPage from "./pages/public/PrivacyPolicyPage";
import TermsPage from "./pages/public/TermsPage";
import BookPage from "./pages/public/BookPage";
import LoginPage from "./pages/auth/LoginPage";
import RegisterPage from "./pages/auth/RegisterPage";
import ForgotPasswordPage from "./pages/auth/ForgotPasswordPage";

const CustomerLayout = lazy(() => import("./pages/customer/CustomerLayout"));
const DashboardHomePage = lazy(() => import("./pages/customer/DashboardHomePage"));
const NewBookingPage = lazy(() => import("./pages/customer/NewBookingPage"));
const MyBookingsPage = lazy(() => import("./pages/customer/MyBookingsPage"));
const BookingDetailPage = lazy(() => import("./pages/customer/BookingDetailPage"));
const ThankYouPage = lazy(() => import("./pages/customer/ThankYouPage"));
const SubscriptionsPage = lazy(() => import("./pages/customer/SubscriptionsPage"));
const VehiclesPage = lazy(() => import("./pages/customer/VehiclesPage"));
const AddressesPage = lazy(() => import("./pages/customer/AddressesPage"));
const SupportPage = lazy(() => import("./pages/customer/SupportPage"));

const CaptainLayout = lazy(() => import("./pages/captain/CaptainLayout"));
const CaptainJobsPage = lazy(() => import("./pages/captain/CaptainJobsPage"));
const CaptainAttendancePage = lazy(() => import("./pages/captain/CaptainAttendancePage"));
const CaptainEarningsPage = lazy(() => import("./pages/captain/CaptainEarningsPage"));
const CaptainProfilePage = lazy(() => import("./pages/captain/CaptainProfilePage"));

const ManagerLayout = lazy(() => import("./pages/manager/ManagerLayout"));
const ManagerDashboardPage = lazy(() => import("./pages/manager/ManagerDashboardPage"));
const ManagerKpiPage = lazy(() => import("./pages/manager/ManagerKpiPage"));
const ManagerNewBookingPage = lazy(() => import("./pages/manager/ManagerNewBookingPage"));
const BookingQueuePage = lazy(() => import("./pages/manager/BookingQueuePage"));
const ManagerCaptainsPage = lazy(() => import("./pages/manager/ManagerCaptainsPage"));
const ManagerSubscribersPage = lazy(() => import("./pages/manager/ManagerSubscribersPage"));
const ManagerInventoryPage = lazy(() => import("./pages/manager/ManagerInventoryPage"));
const ManagerComplaintsPage = lazy(() => import("./pages/manager/ManagerComplaintsPage"));
const ManagerReviewsPage = lazy(() => import("./pages/manager/ManagerReviewsPage"));

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
const AdminWhatsAppPage = lazy(() => import("./pages/admin/AdminWhatsAppPage"));
const AdminCoverageLeadsPage = lazy(() => import("./pages/admin/AdminCoverageLeadsPage"));
const AdminPricingPage = lazy(() => import("./pages/admin/AdminPricingPage"));
const AdminSubscriptionPlansPage = lazy(() => import("./pages/admin/AdminSubscriptionPlansPage"));
const AdminCouponsPage = lazy(() => import("./pages/admin/AdminCouponsPage"));
const AdminComplaintsPage = lazy(() => import("./pages/admin/AdminComplaintsPage"));
const AdminReviewsPage = lazy(() => import("./pages/admin/AdminReviewsPage"));
const AdminAuditLogsPage = lazy(() => import("./pages/admin/AdminAuditLogsPage"));

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
      toastBus.emit?.({ tone: "error", title: "That didn't save", message: getErrorMessage(error) });
    },
  }),
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
        <ToastProvider>
        <ConfirmProvider>
          <ToastContainer />
          <ConfirmDialog />
          <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/services" element={<ServicesPage />} />
            <Route path="/plans" element={<PlansPage />} />
            <Route path="/cancellation-policy" element={<CancellationPolicyPage />} />
            <Route path="/privacy-policy" element={<PrivacyPolicyPage />} />
            <Route path="/terms" element={<TermsPage />} />
            <Route path="/book" element={<BookPage />} />

            <Route element={<GuestOnlyRoute />}>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<RegisterPage />} />
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
                <Route path="vehicles" element={<VehiclesPage />} />
                <Route path="addresses" element={<AddressesPage />} />
                <Route path="support" element={<SupportPage />} />
                <Route path="profile" element={<CustomerProfilePage />} />
                <Route path="notifications" element={<NotificationsPage />} />
              </Route>
            </Route>

            {/* Captain portal */}
            <Route element={<ProtectedRoute allowedRoles={["captain"]} />}>
              <Route path="/captain" element={<CaptainLayout />}>
                <Route index element={<CaptainJobsPage />} />
                <Route path="attendance" element={<CaptainAttendancePage />} />
                <Route path="earnings" element={<CaptainEarningsPage />} />
                <Route path="profile" element={<CaptainProfilePage />} />
                <Route path="notifications" element={<NotificationsPage />} />
              </Route>
            </Route>

            {/* Manager portal */}
            <Route element={<ProtectedRoute allowedRoles={["manager"]} />}>
              <Route path="/manager" element={<ManagerLayout />}>
                <Route index element={<ManagerDashboardPage />} />
                <Route path="kpi" element={<ManagerKpiPage />} />
                <Route path="new-booking" element={<ManagerNewBookingPage />} />
                <Route path="bookings" element={<BookingQueuePage />} />
                {/* Notification deep-links land here — send staff to their
                    real actionable view (the queue, highlighted), not the
                    customer's read-only detail page. */}
                <Route path="bookings/:id" element={<StaffBookingRedirect base="/manager/bookings" />} />
                <Route path="captains" element={<ManagerCaptainsPage />} />
                <Route path="subscribers" element={<ManagerSubscribersPage />} />
                <Route path="inventory" element={<ManagerInventoryPage />} />
                <Route path="complaints" element={<ManagerComplaintsPage />} />
                <Route path="reviews" element={<ManagerReviewsPage />} />
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
                <Route path="coverage-requests" element={<AdminCoverageLeadsPage />} />
                <Route path="pricing" element={<AdminPricingPage />} />
                <Route path="subscription-plans" element={<AdminSubscriptionPlansPage />} />
                <Route path="coupons" element={<AdminCouponsPage />} />
                <Route path="complaints" element={<AdminComplaintsPage />} />
                <Route path="reviews" element={<AdminReviewsPage />} />
                <Route path="audit-logs" element={<AdminAuditLogsPage />} />
                <Route path="profile" element={<ProfilePage />} />
                <Route path="notifications" element={<NotificationsPage />} />
              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </Suspense>
        </ConfirmProvider>
        </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
