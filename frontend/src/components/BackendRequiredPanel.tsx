import type { ReactNode } from "react";

/**
 * Honest "no backend yet" placeholder used across the admin portal.
 *
 * Several admin screens (system stats, verification queue, user directory, audit
 * log, analytics, certificate registry) have real, designed layouts but no backing
 * API — the LifeLink backend exposes 11 routers (auth, patients, donors, family,
 * blood_requests, emergencies, notifications, hospitals, matching, certificates,
 * health) and none of them serve admin-wide data. Rather than inventing rows or a
 * fake success state, every such section renders this panel: it names what's
 * missing and, where useful, the endpoint that would need to exist.
 */
export default function BackendRequiredPanel({
  title = "Backend required",
  description,
  needs,
  icon = "cloud_off",
  className = "",
  compact = false,
}: {
  title?: string;
  description: ReactNode;
  /** Endpoint(s) that would need to exist, shown as a small code-styled list. */
  needs?: string[];
  icon?: string;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div
      role="status"
      className={`flex flex-col items-center text-center gap-space-3 rounded-xl border border-dashed border-outline-variant bg-surface-container-low ${
        compact ? "p-space-6" : "p-space-8 lg:p-space-12"
      } ${className}`}
    >
      <div className="w-12 h-12 rounded-full bg-surface-container flex items-center justify-center text-on-surface-variant">
        <span aria-hidden="true" className="material-symbols-outlined text-[24px]">{icon}</span>
      </div>
      <div className="flex flex-col gap-space-1 max-w-lg">
        <span className="font-title-md text-title-md text-on-surface">{title}</span>
        <p className="font-body-sm text-body-sm text-on-surface-variant">{description}</p>
      </div>
      {needs && needs.length > 0 && (
        <ul className="flex flex-col gap-space-1 mt-space-1">
          {needs.map((n) => (
            <li
              key={n}
              className="font-data-mono text-label-sm text-on-surface-variant bg-surface-container px-space-3 py-1 rounded-lg"
            >
              Needs: {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
