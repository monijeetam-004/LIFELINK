import type { SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import FieldShell, { inputBaseClass, inputBorderClass } from "./FieldShell";
import { BLOOD_TYPES, BLOOD_LABEL, type BloodType } from "../../lib/domain";

export interface SelectOption {
  value: string;
  label: string;
}

type Props = Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "className"> & {
  label: string;
  options: SelectOption[];
  placeholder?: string;
  error?: string | null;
  helperText?: string;
};

export function SelectField({ label, options, placeholder, error, helperText, required, ...rest }: Props) {
  return (
    <FieldShell label={label} error={error} helperText={helperText} required={required}>
      {({ inputId, describedBy }) => (
        <div className="relative">
          <select
            id={inputId}
            required={required}
            aria-invalid={!!error}
            aria-describedby={describedBy}
            className={`${inputBaseClass} ${inputBorderClass(error)} appearance-none pr-9`}
            {...rest}
          >
            {placeholder && (
              <option value="" disabled>
                {placeholder}
              </option>
            )}
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-on-surface-variant pointer-events-none" />
        </div>
      )}
    </FieldShell>
  );
}

/** Blood-group select preset from the canonical BLOOD_TYPES/BLOOD_LABEL used
 * everywhere else in the app (lib/domain.ts) — never a locally-invented list. */
export function BloodGroupField(
  props: Omit<Props, "options"> & { value?: BloodType | ""; includeAnyOption?: boolean },
) {
  const { includeAnyOption, ...rest } = props;
  const options: SelectOption[] = [
    ...(includeAnyOption ? [{ value: "", label: "Any blood type" }] : []),
    ...BLOOD_TYPES.map((bt) => ({ value: bt, label: BLOOD_LABEL[bt] })),
  ];
  return <SelectField {...rest} options={options} placeholder={rest.placeholder ?? "Select blood group"} />;
}
