/**
 * Phase 22: a shared, accessible mobile navigation drawer for the ~16 hand-
 * rolled dashboard/sub-pages (Admin's 8 screens, PatientDashboard,
 * FamilyDashboard, and several patient sub-pages) that were converted 1:1
 * from the Google Stitch export with a desktop-only `hidden md:flex` /
 * `hidden lg:flex` sidebar and NO mobile equivalent at all -- on a real
 * phone, a signed-in user landing on one of these screens had no way to
 * navigate anywhere else in the app (see LIFELINK_PHASE_22_AUDIT.md,
 * "missing mobile navigation").
 *
 * Deliberately a *new*, separate component from `components/shell/MobileNav.tsx`
 * rather than a shared one: that component is tightly coupled to
 * `ShellNavItem`'s `LucideIcon` component values (used by the opt-in
 * `AppShell`, adopted so far only by `HospitalDashboard.tsx`), while every
 * page this component targets already uses Material Symbols icon *name*
 * strings throughout its existing hand-rolled sidebar -- matching that
 * existing visual language is what "no redesign" means here. The dialog
 * semantics (role="dialog", aria-modal, an accessible close button,
 * backdrop-click-to-close) mirror that component's proven pattern; Escape-
 * to-close and initial focus-on-open are added here (see section 11 of the
 * Phase 22 audit) and are safe, uncontroversial additions worth carrying
 * back to the older component too.
 */
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";

export interface MobileNavItem {
  path: string;
  icon: string;
  label: string;
  badge?: number;
}

export interface MobileNavSection {
  heading?: string;
  items: MobileNavItem[];
}

export default function MobileNavDrawer({
  open,
  onClose,
  sections,
  activePath,
  title = "LifeLink",
  subtitle,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  sections: MobileNavSection[];
  activePath: string;
  title?: string;
  subtitle?: string;
  footer?: React.ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    // Move focus into the drawer as soon as it opens -- a keyboard/screen-
    // reader user's focus must not silently stay on a now-hidden trigger
    // behind the overlay.
    closeRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100]"
      role="dialog"
      aria-modal="true"
      aria-label={`${title} navigation`}
    >
      <div className="absolute inset-0 bg-black/40" onMouseDown={onClose} />
      <div className="absolute top-0 left-0 h-full w-[82%] max-w-xs bg-surface-container-lowest shadow-level-3 flex flex-col">
        <div className="h-16 flex items-center justify-between px-space-4 border-b border-border-subtle shrink-0">
          <div className="flex flex-col min-w-0">
            <span className="font-headline-sm text-headline-sm text-on-surface truncate">{title}</span>
            {subtitle && (
              <span className="font-label-sm text-label-sm text-secondary uppercase tracking-wider truncate">
                {subtitle}
              </span>
            )}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close navigation menu"
            className="p-2 -mr-2 rounded-full text-on-surface-variant hover:bg-surface-container-low shrink-0"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto px-space-3 py-space-3" aria-label="Primary">
          {sections.map((section, i) => (
            <div key={section.heading ?? i} className={i > 0 ? "pt-space-3" : undefined}>
              {section.heading && (
                <div className="px-space-3 py-space-1">
                  <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                    {section.heading}
                  </span>
                </div>
              )}
              {section.items.map((n) => (
                <Link
                  key={n.path}
                  to={n.path}
                  onClick={onClose}
                  aria-current={activePath === n.path ? "page" : undefined}
                  className={`flex items-center justify-between gap-space-3 px-space-3 py-space-3 rounded-lg font-label-lg text-label-lg transition-all ${
                    activePath === n.path
                      ? "bg-primary-container text-on-primary font-bold"
                      : "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
                  }`}
                >
                  <span className="flex items-center gap-space-3 min-w-0">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px] shrink-0">{n.icon}</span>
                    <span className="truncate">{n.label}</span>
                  </span>
                  {!!n.badge && (
                    <span className="bg-primary text-on-primary font-label-sm text-label-sm px-space-2 py-0.5 rounded-full font-semibold shrink-0">
                      {n.badge}
                    </span>
                  )}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        {footer && <div className="p-space-4 border-t border-border-subtle shrink-0">{footer}</div>}
      </div>
    </div>
  );
}
