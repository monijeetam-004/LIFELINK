import { type ReactNode, useEffect, useId, useRef } from "react";
import { X } from "lucide-react";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Lightweight centered modal in the LifeLink design language.
 *
 * Shared by every real dialog in the app (BloodRequestModal, SosModal,
 * HospitalInventoryModal, ConfirmationDialog, RequestTrackDrawer,
 * EmergencyTrackDrawer) -- so the dialog semantics, focus-on-open, a real
 * (not faked) Tab focus trap, and focus-return-on-close added here (Phase 22,
 * section 11) apply everywhere at once rather than needing seven separate
 * fixes.
 */
export default function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: string;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    // Remember what had focus before the dialog opened, move focus into the
    // dialog now, and return it there when the dialog closes -- otherwise a
    // keyboard/screen-reader user's focus silently lands on <body>.
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const container = dialogRef.current;
      if (!container) return;
      const focusable = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (el) => el.offsetParent !== null,
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      // A real Tab-cycling trap (not a fake one that just refocuses on blur):
      // wrap at the two ends so focus never silently escapes to the page
      // behind the overlay, and otherwise let Tab behave normally.
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/40"
      onMouseDown={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`w-full ${width} bg-surface rounded-2xl shadow-2xl border border-border-subtle max-h-[90vh] overflow-y-auto`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle">
          <h3 id={titleId} className="font-headline-md text-headline-md text-on-surface">
            {title}
          </h3>
          <button
            ref={closeRef}
            className="p-1.5 rounded-full text-on-surface-variant hover:bg-surface-container-low"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}

export const fieldClass =
  "w-full px-3.5 py-2.5 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary";
export const labelClass =
  "block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5";
export const primaryButtonClass =
  "bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-5 py-2.5 rounded-lg transition-all active:scale-[0.99] disabled:opacity-60 flex items-center justify-center gap-2";
export const ghostButtonClass =
  "border border-border-subtle text-on-surface font-label-md text-label-md px-5 py-2.5 rounded-lg hover:bg-surface-container-low transition-colors";
