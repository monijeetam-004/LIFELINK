import { type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { useAuth } from "../lib/auth";
import type { Role } from "../lib/types";
import AccountMenu from "./AccountMenu";
import AccessDenied from "../pages/errors/AccessDenied";

/**
 * Client-side route guard. UX only — the backend is the authority on access.
 * Redirects unauthenticated users to /login, and users without an allowed
 * role to their own home dashboard.
 */
export default function RequireAuth({
  roles,
  children,
}: {
  roles?: Role[];
  children: ReactNode;
}) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  // Hardening: the backend now refuses every route except "me", sign out and
  // change password until the address is verified (403); send the user to
  // the page that can fix that instead of a dashboard full of errors.
  if (!user.is_email_verified) {
    return <Navigate to="/verify-email" replace state={{ from: location.pathname }} />;
  }

  // Rendered inline (not a silent redirect) so the user gets an explicit,
  // actionable "you don't have access" page rather than being bounced
  // somewhere else with no explanation.
  if (roles && !roles.includes(user.role)) {
    return <AccessDenied />;
  }

  return (
    <>
      {children}
      <AccountMenu />
    </>
  );
}
