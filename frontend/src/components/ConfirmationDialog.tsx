import { Loader2 } from "lucide-react";
import Modal, { ghostButtonClass, primaryButtonClass } from "./Modal";

/**
 * Reusable "are you sure?" dialog. Renders on top of Modal so it shares the
 * same overlay/keyboard(Esc)/focus behavior. Callers own the async action —
 * this component only handles the confirm/cancel UI and busy state.
 */
export default function ConfirmationDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  busy = false,
  errorMessage,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  errorMessage?: string | null;
}) {
  return (
    <Modal open={open} onClose={busy ? () => {} : onClose} title={title} width="max-w-sm">
      <div className="space-y-4">
        {description && <p className="text-body-sm text-on-surface-variant">{description}</p>}
        {errorMessage && (
          <p className="text-error text-body-sm" role="alert">
            {errorMessage}
          </p>
        )}
        <div className="flex justify-end gap-3 pt-1">
          <button type="button" className={ghostButtonClass} onClick={onClose} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={
              destructive
                ? "bg-error hover:bg-on-error-container text-on-error font-label-md text-label-md px-5 py-2.5 rounded-lg transition-all active:scale-[0.99] disabled:opacity-60 flex items-center justify-center gap-2"
                : primaryButtonClass
            }
            onClick={onConfirm}
            disabled={busy}
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
