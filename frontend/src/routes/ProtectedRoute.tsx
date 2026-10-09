import { useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ErrorState, PageLoader } from "../components/ui";
import { ROLE_BASE_PATH } from "../lib/constants";
import type { UserRole } from "../types";

/** A saved session we couldn't check (offline / server down): the tokens
 *  are still there, so this is "try again", never a trip to /login. */
function SessionCheckFailed() {
  const { refreshUser } = useAuth();
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex min-h-[60vh] items-center justify-center p-4">
      <ErrorState
        className="w-full max-w-sm"
        message="Can't reach Blussit right now — check your connection."
        busy={busy}
        onRetry={() => {
          setBusy(true);
          void refreshUser().finally(() => setBusy(false));
        }}
      />
    </div>
  );
}

export function ProtectedRoute({ allowedRoles }: { allowedRoles: UserRole[] }) {
  const { isAuthenticated, isLoading, user, sessionUnverified, sessionNotice } = useAuth();
  const location = useLocation();

  if (isLoading) return <PageLoader />;

  if (!isAuthenticated) {
    if (sessionUnverified) return <SessionCheckFailed />;
    return <Navigate to="/login" state={{ from: location, notice: sessionNotice }} replace />;
  }

  if (user && !allowedRoles.includes(user.role)) {
    return <Navigate to="/" replace />;
  }

  // A staff-created account (temp password) must set a real one before
  // going anywhere else — the profile page surfaces the change-password
  // form prominently for this. See User.must_change_password.
  // Customers sign in by code and never need a password (2026-10-09).
  if (user?.must_change_password && user.role !== "customer") {
    const profilePath = `${ROLE_BASE_PATH[user.role] ?? "/app"}/profile`;
    if (location.pathname !== profilePath) {
      return <Navigate to={profilePath} replace />;
    }
  }

  return <Outlet />;
}

export function GuestOnlyRoute() {
  const { isAuthenticated, isLoading, user } = useAuth();
  if (isLoading) return <PageLoader />;
  // Role-aware: a logged-in manager clicking "Login" used to bounce
  // /login → /app → (customer-only guard) → the landing page.
  if (isAuthenticated) return <Navigate to={ROLE_BASE_PATH[user?.role ?? "customer"] ?? "/app"} replace />;
  return <Outlet />;
}
