import { useEffect, useRef } from "react";
import { NavLink } from "react-router-dom";
import { X } from "lucide-react";
import type { ShellNavItem } from "./navTypes";

/** Full-screen slide-over nav for small screens, opened from Topbar's menu button. */
export default function MobileNav({
  open,
  onClose,
  items,
}: {
  open: boolean;
  onClose: () => void;
  items: ShellNavItem[];
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  // Phase 22: matches components/MobileNavDrawer.tsx's own Escape-to-close
  // and focus-on-open -- added there first for the Stitch-ported dashboard
  // pages, carried back here since both dialogs share the same requirement
  // (section 11) and neither had it before.
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/40" onMouseDown={onClose} />
      <div className="absolute top-0 left-0 h-full w-[80%] max-w-xs bg-surface shadow-2xl flex flex-col">
        <div className="h-16 flex items-center justify-between px-5 border-b border-border-subtle">
          <span className="font-headline-sm text-headline-sm text-primary">LifeLink</span>
          <button
            ref={closeRef}
            onClick={onClose}
            aria-label="Close navigation menu"
            className="p-1.5 rounded-full text-on-surface-variant hover:bg-surface-container-low"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-2 space-y-0.5" aria-label="Primary">
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onClose}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-3 rounded-lg text-body-md transition-colors ${
                    isActive
                      ? "bg-primary/10 text-primary font-label-md"
                      : "text-on-surface-variant hover:bg-surface-container-low"
                  }`
                }
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                <span className="truncate">{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
