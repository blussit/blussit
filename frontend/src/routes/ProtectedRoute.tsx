import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { PageLoader } from "../components/ui";
import { ROLE_BASE_PATH } from "../lib/constants";
import type { UserRole } from "../types";

export function ProtectedRoute({ allowedRoles }: { allowedRoles: UserRole[] }) {
  const { isAuthenticated, isLoading, user } = useAuth();
  const location = useLocation();

  if (isLoading) return <PageLoader />;

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (user && !allowedRoles.includes(user.role)) {
    return <Navigate to="/" replace />;
  }

  // A staff-created account (temp password) must set a real one before
  // going anywhere else — the profile page surfaces the change-password
  // form prominently for this. See User.must_change_password.
  if (user?.must_change_password) {
    const profilePath = `${ROLE_BASE_PATH[user.role] ?? "/app"}/profile`;
    if (location.pathname !== profilePath) {
      return <Navigate to={profilePath} replace />;
    }
  }

  return <Outlet />;
}

export function GuestOnlyRoute() {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading) return <PageLoader />;
  if (isAuthenticated) return <Navigate to="/app" replace />;
  return <Outlet />;
}
