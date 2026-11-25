/**
 * Donor Experience Dashboard (mobile reference layout) — retired.
 *
 * This page was a raw, un-converted port of the Stitch mobile mockup
 * (donor_dashboard_mobile/code.html): a fake "94% Donation Readiness Score"
 * with no backend analog, a hardcoded "O-Negative Blood Needed... St. Jude
 * Regional" emergency card with a non-functional "Respond Now" button, fake
 * "Community Impact" stats (Lives Saved, Points, Level "Gold" — no points or
 * level system exists anywhere in the backend), a fake donor name/photo, and
 * a "Sign Out" button wired to nothing. None of it was ever backed by real
 * data, unlike every other donor page in this app.
 *
 * DonorDashboard.tsx (the /donor route) is already the real, fully wired
 * donor dashboard AND is itself responsive — it has its own md:hidden bottom
 * nav for narrow viewports. Rather than maintain two parallel dashboards
 * (one real, one fabricated), this route now redirects to the real one.
 */
import { Navigate } from "react-router-dom";

export default function DonorDashboardMobile() {
  return <Navigate to="/donor" replace />;
}
