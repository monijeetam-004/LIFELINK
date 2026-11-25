import { useId, type InputHTMLAttributes } from "react";

type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "type" | "className"> & {
  label: string;
  error?: string | null;
  helperText?: string;
};

/** Native checkbox — kept native (not a div reimplementation) for full
 * keyboard/screen-reader behavior for free. */
export function CheckboxField({ label, error, helperText, ...rest }: CheckboxProps) {
  const id = useId();
  const describedBy = error ? `${id}-error` : helperText ? `${id}-helper` : undefined;
  return (
    <div>
      <label htmlFor={id} className="flex items-start gap-2.5 cursor-pointer select-none">
        <input
          id={id}
          type="checkbox"
          aria-invalid={!!error}
          aria-describedby={describedBy}
          className="mt-0.5 h-4.5 w-4.5 rounded border-outline-variant text-primary focus:ring-2 focus:ring-primary/40 disabled:opacity-60"
          {...rest}
        />
        <span className="text-body-sm text-on-surface">{label}</span>
      </label>
      {error && (
        <p id={`${id}-error`} role="alert" className="text-error text-body-sm mt-1 ml-7">
          {error}
        </p>
      )}
      {!error && helperText && (
        <p id={`${id}-helper`} className="text-on-surface-variant text-body-sm mt-1 ml-7">
          {helperText}
        </p>
      )}
    </div>
  );
}

export interface RadioOption {
  value: string;
  label: string;
  description?: string;
}

/** Accessible radio group — native inputs, arrow-key navigation comes free
 * from the browser when options share a `name`. */
export function RadioGroupField({
  legend,
  name,
  options,
  value,
  onChange,
  error,
  disabled,
  layout = "vertical",
}: {
  legend: string;
  name: string;
  options: RadioOption[];
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  disabled?: boolean;
  layout?: "vertical" | "horizontal";
}) {
  return (
    <fieldset>
      <legend className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
        {legend}
      </legend>
      <div className={layout === "horizontal" ? "flex flex-wrap gap-4" : "flex flex-col gap-2.5"}>
        {options.map((opt) => (
          <label key={opt.value} className="flex items-start gap-2.5 cursor-pointer select-none">
            <input
              type="radio"
              name={name}
              value={opt.value}
              checked={value === opt.value}
              disabled={disabled}
              onChange={() => onChange(opt.value)}
              className="mt-0.5 h-4.5 w-4.5 border-outline-variant text-primary focus:ring-2 focus:ring-primary/40 disabled:opacity-60"
            />
            <span>
              <span className="block text-body-sm text-on-surface">{opt.label}</span>
              {opt.description && (
                <span className="block text-on-surface-variant text-[11px] leading-tight">{opt.description}</span>
              )}
            </span>
          </label>
        ))}
      </div>
      {error && (
        <p role="alert" className="text-error text-body-sm mt-1">
          {error}
        </p>
      )}
    </fieldset>
  );
}

/** Labeled on/off switch, built on a visually-hidden native checkbox so it
 * keeps keyboard/Space-to-toggle/screen-reader behavior for free instead of
 * reimplementing it on a <button>. */
export function ToggleField({
  label,
  description,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <label htmlFor={id} className="flex items-center justify-between gap-4 cursor-pointer select-none">
      <span>
        <span className="block font-label-md text-label-md text-on-surface">{label}</span>
        {description && <span className="block text-xs text-on-surface-variant">{description}</span>}
      </span>
      <span className="relative inline-block w-12 h-7 flex-shrink-0">
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className="absolute inset-0 rounded-full bg-surface-container-high peer-checked:bg-success transition-colors peer-disabled:opacity-60"
        />
        <span
          aria-hidden="true"
          className="absolute top-1 left-1 w-5 h-5 rounded-full bg-white shadow transition-transform peer-checked:translate-x-5"
        />
      </span>
    </label>
  );
}
