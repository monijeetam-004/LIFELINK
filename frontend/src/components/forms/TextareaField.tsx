import type { TextareaHTMLAttributes } from "react";
import FieldShell, { inputBaseClass, inputBorderClass } from "./FieldShell";

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id" | "className"> & {
  label: string;
  error?: string | null;
  helperText?: string;
};

export function TextareaField({ label, error, helperText, required, rows = 4, ...rest }: Props) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required}>
      {({ inputId, describedBy }) => (
        <textarea
          id={inputId}
          rows={rows}
          required={required}
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className={`${inputBaseClass} ${inputBorderClass(error)} resize-y`}
          {...rest}
        />
      )}
    </FieldShell>
  );
}
