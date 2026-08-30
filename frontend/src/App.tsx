import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ToastContainer } from "./components/shared/ToastContainer";
import { ConfirmProvider } from "./context/ConfirmContext";
import { ConfirmDialog } from "./components/shared/ConfirmDialog";
import { ProtectedRoute, GuestOnlyRoute } from "./routes/ProtectedRoute";

import LandingPage from "./pages/public/LandingPage";
import ServicesPage from "./pages/public/ServicesPage";
import PlansPage from "./pages/public/PlansPage";
import BookPage from "./pages/public/BookPage";
import LoginPage from "./pages/auth/LoginPage";
import RegisterPage from "./pages/auth/RegisterPage";
import ForgotPasswordPage from "./pages/auth/ForgotPasswordPage";

import CustomerLayout from "./pages/customer/CustomerLayout";
import DashboardHomePage from "./pages/customer/DashboardHomePage";
import NewBookingPage from "./pages/customer/NewBookingPage";
import MyBookingsPage from "./pages/customer/MyBookingsPage";
import BookingDetailPage from "./pages/customer/BookingDetailPage";
import ThankYouPage from "./pages/customer/ThankYouPage";
import SubscriptionsPage from "./pages/customer/SubscriptionsPage";
import VehiclesPage from "./pages/customer/VehiclesPage";
import AddressesPage from "./pages/customer/AddressesPage";
import SupportPage from "./pages/customer/SupportPage";

import CaptainLayout from "./pages/captain/CaptainLayout";
import CaptainJobsPage from "./pages/captain/CaptainJobsPage";
import CaptainAttendancePage from "./pages/captain/CaptainAttendancePage";
import CaptainEarningsPage from "./pages/captain/CaptainEarningsPage";

import ManagerLayout from "./pages/manager/ManagerLayout";
import ManagerDashboardPage from "./pages/manager/ManagerDashboardPage";
import ManagerKpiPage from "./pages/manager/ManagerKpiPage";
import ManagerNewBookingPage from "./pages/manager/ManagerNewBookingPage";
import BookingQueuePage from "./pages/manager/BookingQueuePage";
import ManagerCaptainsPage from "./pages/manager/ManagerCaptainsPage";
import ManagerSubscribersPage from "./pages/manager/ManagerSubscribersPage";
import ManagerInventoryPage from "./pages/manager/ManagerInventoryPage";
import ManagerComplaintsPage from "./pages/manager/ManagerComplaintsPage";
import ManagerReviewsPage from "./pages/manager/ManagerReviewsPage";

import AdminLayout from "./pages/admin/AdminLayout";
import AdminDashboardPage from "./pages/admin/AdminDashboardPage";
import AdminBookingsPage from "./pages/admin/AdminBookingsPage";
import AdminUsersPage from "./pages/admin/AdminUsersPage";
import AdminServiceCentersPage from "./pages/admin/AdminServiceCentersPage";
import AdminSlotCapacityPage from "./pages/admin/AdminSlotCapacityPage";
import AdminServicesPage from "./pages/admin/AdminServicesPage";
import AdminVehicleTypesPage from "./pages/admin/AdminVehicleTypesPage";
import AdminComboOffersPage from "./pages/admin/AdminComboOffersPage";
import AdminHomepageSettingsPage from "./pages/admin/AdminHomepageSettingsPage";
import AdminContactMessagesPage from "./pages/admin/AdminContactMessagesPage";
import AdminCoverageLeadsPage from "./pages/admin/AdminCoverageLeadsPage";
import AdminPricingPage from "./pages/admin/AdminPricingPage";
import AdminSubscriptionPlansPage from "./pages/admin/AdminSubscriptionPlansPage";
import AdminCouponsPage from "./pages/admin/AdminCouponsPage";
import AdminComplaintsPage from "./pages/admin/AdminComplaintsPage";
import AdminReviewsPage from "./pages/admin/AdminReviewsPage";
import AdminAuditLogsPage from "./pages/admin/AdminAuditLogsPage";

import ProfilePage from "./pages/shared/ProfilePage";
import NotificationsPage from "./pages/shared/NotificationsPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 } },
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
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/services" element={<ServicesPage />} />
            <Route path="/plans" element={<PlansPage />} />
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
                <Route path="profile" element={<ProfilePage />} />
                <Route path="notifications" element={<NotificationsPage />} />
              </Route>
            </Route>

            {/* Captain portal */}
            <Route element={<ProtectedRoute allowedRoles={["captain"]} />}>
              <Route path="/captain" element={<CaptainLayout />}>
                <Route index element={<CaptainJobsPage />} />
                <Route path="attendance" element={<CaptainAttendancePage />} />
                <Route path="earnings" element={<CaptainEarningsPage />} />
                <Route path="profile" element={<ProfilePage />} />
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
                <Route path="bookings/:id" element={<BookingDetailPage />} />
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
                <Route path="bookings/:id" element={<BookingDetailPage />} />
                <Route path="users" element={<AdminUsersPage />} />
                <Route path="service-centers" element={<AdminServiceCentersPage />} />
                <Route path="service-centers/:centerId/capacity" element={<AdminSlotCapacityPage />} />
                <Route path="services" element={<AdminServicesPage />} />
                <Route path="vehicle-types" element={<AdminVehicleTypesPage />} />
                <Route path="combo-offers" element={<AdminComboOffersPage />} />
                <Route path="homepage" element={<AdminHomepageSettingsPage />} />
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
        </ConfirmProvider>
        </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
