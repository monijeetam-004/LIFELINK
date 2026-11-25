import { type InputHTMLAttributes, useState } from "react";
import { Eye, EyeOff, Search } from "lucide-react";
import FieldShell, { inputBaseClass, inputBorderClass } from "./FieldShell";

type BaseProps = Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "type" | "className"> & {
  label: string;
  error?: string | null;
  helperText?: string;
  loading?: boolean;
};

/** Plain single-line text input. */
export function TextField({ label, error, helperText, loading, required, ...rest }: BaseProps) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required} loading={loading}>
      {({ inputId, describedBy }) => (
        <input
          id={inputId}
          type="text"
          required={required}
          disabled={rest.disabled || loading}
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className={`${inputBaseClass} ${inputBorderClass(error)}`}
          {...rest}
        />
      )}
    </FieldShell>
  );
}

/** Email input — sets type/inputMode/autocomplete for the right mobile keyboard. */
export function EmailField({ label, error, helperText, loading, required, ...rest }: BaseProps) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required} loading={loading}>
      {({ inputId, describedBy }) => (
        <input
          id={inputId}
          type="email"
          inputMode="email"
          autoComplete={rest.autoComplete ?? "email"}
          required={required}
          disabled={rest.disabled || loading}
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className={`${inputBaseClass} ${inputBorderClass(error)}`}
          {...rest}
        />
      )}
    </FieldShell>
  );
}

/** Phone input — tel keyboard on mobile; validation pattern left to the caller. */
export function PhoneField({ label, error, helperText, loading, required, ...rest }: BaseProps) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required} loading={loading}>
      {({ inputId, describedBy }) => (
        <input
          id={inputId}
          type="tel"
          inputMode="tel"
          autoComplete={rest.autoComplete ?? "tel"}
          required={required}
          disabled={rest.disabled || loading}
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className={`${inputBaseClass} ${inputBorderClass(error)}`}
          {...rest}
        />
      )}
    </FieldShell>
  );
}

/** Number input. */
export function NumberField({ label, error, helperText, loading, required, ...rest }: BaseProps) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required} loading={loading}>
      {({ inputId, describedBy }) => (
        <input
          id={inputId}
          type="number"
          inputMode="numeric"
          required={required}
          disabled={rest.disabled || loading}
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className={`${inputBaseClass} ${inputBorderClass(error)}`}
          {...rest}
        />
      )}
    </FieldShell>
  );
}

/** Password input with a show/hide toggle (keyboard accessible, tabIndex=-1
 * so Tab order skips straight to the next field like a native input). */
export function PasswordField({ label, error, helperText, loading, required, ...rest }: BaseProps) {
  const [visible, setVisible] = useState(false);
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required} loading={loading}>
      {({ inputId, describedBy }) => (
        <div className="relative">
          <input
            id={inputId}
            type={visible ? "text" : "password"}
            autoComplete={rest.autoComplete ?? "current-password"}
            required={required}
            disabled={rest.disabled || loading}
            aria-invalid={!!error}
            aria-describedby={describedBy}
            className={`${inputBaseClass} ${inputBorderClass(error)} pr-11`}
            {...rest}
          />
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setVisible((v) => !v)}
            aria-label={visible ? "Hide password" : "Show password"}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-primary transition-colors"
          >
            {visible ? <EyeOff className="h-4.5 w-4.5" /> : <Eye className="h-4.5 w-4.5" />}
          </button>
        </div>
      )}
    </FieldShell>
  );
}

/** Search input with a leading icon; intended for filter/search bars. */
export function SearchField({ label, error, helperText, loading, required, ...rest }: BaseProps) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required} loading={loading}>
      {({ inputId, describedBy }) => (
        <div className="relative">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-on-surface-variant pointer-events-none" />
          <input
            id={inputId}
            type="search"
            required={required}
            disabled={rest.disabled || loading}
            aria-invalid={!!error}
            aria-describedby={describedBy}
            className={`${inputBaseClass} ${inputBorderClass(error)} pl-10`}
            {...rest}
          />
        </div>
      )}
    </FieldShell>
  );
}
