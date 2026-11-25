import { Link, useLocation, useNavigate } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { useAuth } from "../../lib/auth";
import { homeForRole } from "../../lib/roles";

/**
 * 403 — shown (not silently redirected) when a signed-in user's role
 * doesn't match a route's allowed roles. Client-side UX only; the backend
 * remains the real authority on access.
 */
export default function AccessDenied() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation() as { pathname: string; state?: { from?: string } };
  const attempted = location.state?.from ?? location.pathname;

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-margin-mobile">
      <div className="text-center max-w-md">
        <ShieldAlert className="h-12 w-12 text-error mx-auto mb-4" />
        <p className="font-display-lg text-display-lg text-error">403</p>
        <h1 className="font-headline-md text-headline-md text-on-surface mt-2">Access denied</h1>
        <p className="text-on-surface-variant mt-2">
          {user
            ? `Your account (${user.role.toLowerCase()}) doesn't have access to ${attempted ?? "this page"}.`
            : "You don't have permission to view this page."}
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          {user ? (
            <button
              type="button"
              onClick={() => navigate(homeForRole(user.role), { replace: true })}
              className="bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-lg transition-all"
            >
              Go to my dashboard
            </button>
          ) : (
            <Link
              to="/login"
              className="bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-lg transition-all"
            >
              Sign in
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
