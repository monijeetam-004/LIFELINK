import { type ReactNode, useEffect } from "react";
import { X } from "lucide-react";

/**
 * Generic slide-in side panel in the LifeLink design language. Distinct
 * from the feature-specific EmergencyTrackDrawer/RequestTrackDrawer (which
 * carry their own domain content) — this is the reusable shell for any
 * future side-panel need.
 */
export default function Drawer({
  open,
  onClose,
  title,
  children,
  side = "right",
  width = "max-w-md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  side?: "left" | "right";
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const sideClass = side === "right" ? "right-0" : "left-0";

  return (
    <div className="fixed inset-0 z-[60] print:hidden" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/40" onMouseDown={onClose} />
      <div
        className={`absolute top-0 ${sideClass} h-full w-full ${width} bg-surface shadow-2xl flex flex-col animate-[slideIn_0.2s_ease-out]`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle flex-shrink-0">
          <h3 className="font-headline-md text-headline-md text-on-surface">{title}</h3>
          <button
            className="p-1.5 rounded-full text-on-surface-variant hover:bg-surface-container-low"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-6">{children}</div>
      </div>
    </div>
  );
}
