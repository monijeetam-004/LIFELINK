import { useNavigate, useLocation } from "react-router-dom";
import { Clock } from "lucide-react";

/**
 * Shown when an authenticated request comes back 401 mid-session (the
 * session cookie expired or was revoked server-side) rather than silently
 * bouncing to /login with no explanation.
 */
export default function SessionExpired() {
  const navigate = useNavigate();
  const location = useLocation() as { state?: { from?: string } };
  const from = location.state?.from;

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-margin-mobile">
      <div className="text-center max-w-md">
        <Clock className="h-12 w-12 text-secondary mx-auto mb-4" />
        <h1 className="font-headline-md text-headline-md text-on-surface">Your session has expired</h1>
        <p className="text-on-surface-variant mt-2">
          For your security, you've been signed out after a period of inactivity. Sign in again to continue.
        </p>
        <button
          type="button"
          onClick={() => navigate("/login", { replace: true, state: from ? { from } : undefined })}
          className="mt-6 bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-lg transition-all"
        >
          Sign in again
        </button>
      </div>
    </div>
  );
}
