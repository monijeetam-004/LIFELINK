import type { InputHTMLAttributes } from "react";
import FieldShell, { inputBaseClass, inputBorderClass } from "./FieldShell";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "type" | "className"> & {
  label: string;
  error?: string | null;
  helperText?: string;
};

export function DateField({ label, error, helperText, required, ...rest }: Props) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required}>
      {({ inputId, describedBy }) => (
        <input
          id={inputId}
          type="date"
          required={required}
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className={`${inputBaseClass} ${inputBorderClass(error)}`}
          {...rest}
        />
      )}
    </FieldShell>
  );
}

export function TimeField({ label, error, helperText, required, ...rest }: Props) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required}>
      {({ inputId, describedBy }) => (
        <input
          id={inputId}
          type="time"
          required={required}
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className={`${inputBaseClass} ${inputBorderClass(error)}`}
          {...rest}
        />
      )}
    </FieldShell>
  );
}
