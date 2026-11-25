import { type ReactNode, useId } from "react";
import { Loader2 } from "lucide-react";

export interface FieldShellProps {
  label: string;
  htmlFor?: string;
  error?: string | null;
  helperText?: string;
  required?: boolean;
  loading?: boolean;
  children: (ids: { inputId: string; describedBy: string | undefined }) => ReactNode;
}

/**
 * Shared label + error + helper-text wrapper every form field renders
 * through, so validation/error/loading/a11y behavior stays consistent
 * across the whole field library without each field re-implementing it.
 */
export default function FieldShell({
  label,
  htmlFor,
  error,
  helperText,
  required,
  loading,
  children,
}: FieldShellProps) {
  const autoId = useId();
  const inputId = htmlFor ?? autoId;
  const errorId = error ? `${inputId}-error` : undefined;
  const helperId = helperText ? `${inputId}-helper` : undefined;
  const describedBy = [errorId, helperId].filter(Boolean).join(" ") || undefined;

  return (
    <div className="w-full">
      <label
        htmlFor={inputId}
        className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5"
      >
        {label}
        {required && (
          <span className="text-error ml-0.5" aria-hidden="true">
            *
          </span>
        )}
      </label>
      <div className="relative">
        {children({ inputId, describedBy })}
        {loading && (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 animate-spin text-on-surface-variant" />
        )}
      </div>
      {error && (
        <p id={errorId} role="alert" className="text-error text-body-sm mt-1">
          {error}
        </p>
      )}
      {!error && helperText && (
        <p id={helperId} className="text-on-surface-variant text-body-sm mt-1">
          {helperText}
        </p>
      )}
    </div>
  );
}

/** Shared input classes — matches components/Modal.tsx's fieldClass so the
 * new field library looks identical to existing hand-written inputs. */
export const inputBaseClass =
  "w-full px-3.5 py-2.5 rounded-lg border bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary disabled:opacity-60 disabled:cursor-not-allowed transition-colors";

export function inputBorderClass(hasError?: string | null): string {
  return hasError ? "border-error focus:ring-error/30 focus:border-error" : "border-outline-variant";
}
