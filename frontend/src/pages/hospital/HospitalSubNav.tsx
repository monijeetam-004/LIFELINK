import { Link } from "react-router-dom";

import NotificationsBell from "../../components/NotificationsBell";

const iconLinkClass =
  "p-2 rounded-full text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface transition-colors";

/**
 * Shared lightweight top bar for the standalone hospital sub-screens
 * (Inventory Console, Donor Directory, Request Detail, Mobile Analytics).
 * Every link here is a real route already registered in App.tsx.
 */
export default function HospitalSubNav({
  title,
  subtitle,
  backTo = "/hospital",
  backLabel = "Back to Hospital Operations",
}: {
  title: string;
  subtitle?: string;
  backTo?: string;
  backLabel?: string;
}) {
  return (
    <header className="sticky top-0 z-40 bg-surface/95 backdrop-blur border-b border-border-subtle">
      <div className="flex items-center justify-between gap-2 px-4 sm:px-6 lg:px-10 h-16">
        <div className="flex items-center gap-2 sm:gap-3 min-w-0">
          <Link to={backTo} aria-label={backLabel} className={iconLinkClass + " -ml-2 shrink-0"}>
            <span aria-hidden="true" className="material-symbols-outlined">arrow_back</span>
          </Link>
          <div className="min-w-0">
            <p className="font-headline-sm text-headline-sm text-on-surface truncate leading-tight">
              {title}
            </p>
            {subtitle && (
              <p className="text-body-sm text-on-surface-variant truncate hidden sm:block">
                {subtitle}
              </p>
            )}
          </div>
        </div>
        <nav className="flex items-center gap-0.5 sm:gap-1 shrink-0" aria-label="Quick links">
          {/* Phase G: was "/analytics" -- the conceptual-illustration page
              (fixed demo numbers). Hospital operators get their real,
              backend-computed analytics instead. */}
          <Link to="/hospital/analytics-mobile" aria-label="Analytics" className={iconLinkClass}>
            <span aria-hidden="true" className="material-symbols-outlined">analytics</span>
          </Link>
          <NotificationsBell buttonClassName={iconLinkClass + " relative"} />
          <Link to="/settings" aria-label="Settings" className={iconLinkClass}>
            <span aria-hidden="true" className="material-symbols-outlined">settings</span>
          </Link>
          <Link to="/profile" aria-label="Profile" className={iconLinkClass}>
            <span aria-hidden="true" className="material-symbols-outlined">account_circle</span>
          </Link>
        </nav>
      </div>
    </header>
  );
}
