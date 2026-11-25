/**
 * Shared sticky header for the standalone donor detail pages under src/pages/donor/
 * (Eligibility Center, Ready Console, Milestones & History, Identity Verification).
 *
 * Deliberately plain and real: unlike the Stitch export's fabricated sidebar
 * ("Dr. Sarah Al-Mansoor", "Clinical Node #04", a fake system-health readout),
 * this only shows a back link to the real donor dashboard, the page title, and
 * the same live NotificationsBell / profile / help links used elsewhere in the app.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import NotificationsBell from "./NotificationsBell";

export default function DonorSectionHeader({
  title,
  subtitle,
  icon,
}: {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-30 bg-surface/90 backdrop-blur-md border-b border-border-subtle">
      <div className="max-w-6xl mx-auto px-4 md:px-margin-desktop h-16 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            to="/donor"
            className="p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant flex-shrink-0"
            aria-label="Back to donor dashboard"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          {icon && <span className="text-primary flex-shrink-0 hidden sm:inline-flex">{icon}</span>}
          <div className="min-w-0">
            <h1 className="font-headline-md text-headline-md text-on-surface truncate leading-tight">
              {title}
            </h1>
            {subtitle && (
              <p className="hidden sm:block text-body-sm text-on-surface-variant truncate">
                {subtitle}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <Link
            to="/help"
            className="p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant transition-colors"
            aria-label="Help"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[20px]">help_outline</span>
          </Link>
          <NotificationsBell buttonClassName="relative p-2 text-on-surface-variant hover:bg-surface-container-low rounded-full transition-colors" />
          <Link
            to="/profile"
            className="p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant transition-colors"
            aria-label="Profile"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[20px]">account_circle</span>
          </Link>
        </div>
      </div>
    </header>
  );
}
