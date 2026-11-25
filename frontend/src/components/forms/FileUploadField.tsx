import { useId, useRef, useState, type DragEvent } from "react";
import { UploadCloud, File as FileIcon, X } from "lucide-react";

/**
 * Drag-and-drop-or-click file picker. Caller owns the actual upload
 * (multipart request) and validation result — this component only handles
 * selection, local size/count checks and the accessible file list UI.
 */
export function FileUploadField({
  label,
  accept,
  multiple = false,
  maxFiles,
  maxSizeBytes,
  files,
  onChange,
  error,
  helperText,
  disabled,
}: {
  label: string;
  accept?: string;
  multiple?: boolean;
  maxFiles?: number;
  maxSizeBytes?: number;
  files: File[];
  onChange: (files: File[]) => void;
  error?: string | null;
  helperText?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  function validateAndSet(selected: FileList | File[]) {
    const incoming = Array.from(selected);
    const combined = multiple ? [...files, ...incoming] : incoming.slice(0, 1);
    if (maxFiles && combined.length > maxFiles) {
      setLocalError(`You can attach at most ${maxFiles} file${maxFiles === 1 ? "" : "s"}.`);
      return;
    }
    if (maxSizeBytes) {
      const tooBig = combined.find((f) => f.size > maxSizeBytes);
      if (tooBig) {
        setLocalError(`${tooBig.name} is larger than the ${Math.round(maxSizeBytes / 1_048_576)}MB limit.`);
        return;
      }
    }
    setLocalError(null);
    onChange(combined);
  }

  function removeAt(index: number) {
    onChange(files.filter((_, i) => i !== index));
  }

  const shownError = error ?? localError;

  return (
    <div>
      <label
        htmlFor={id}
        className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5"
      >
        {label}
      </label>
      <div
        onDragOver={(e: DragEvent) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e: DragEvent) => {
          e.preventDefault();
          setDragOver(false);
          if (!disabled && e.dataTransfer.files.length) validateAndSet(e.dataTransfer.files);
        }}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !disabled) {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        className={`flex flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed px-4 py-6 text-center cursor-pointer transition-colors ${
          dragOver ? "border-primary bg-primary/5" : "border-outline-variant hover:border-primary/40"
        } ${disabled ? "opacity-60 cursor-not-allowed" : ""}`}
      >
        <UploadCloud className="h-6 w-6 text-on-surface-variant" />
        <p className="text-body-sm text-on-surface">
          <span className="text-primary font-label-md">Click to upload</span> or drag and drop
        </p>
        {helperText && <p className="text-on-surface-variant text-[11px]">{helperText}</p>}
        <input
          ref={inputRef}
          id={id}
          type="file"
          accept={accept}
          multiple={multiple}
          disabled={disabled}
          className="sr-only"
          onChange={(e) => e.target.files && validateAndSet(e.target.files)}
        />
      </div>

      {files.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {files.map((f, i) => (
            <li
              key={`${f.name}-${i}`}
              className="flex items-center gap-2 text-body-sm bg-surface-container-low rounded-lg px-3 py-2"
            >
              <FileIcon className="h-4 w-4 text-on-surface-variant flex-shrink-0" />
              <span className="truncate flex-1">{f.name}</span>
              <span className="text-on-surface-variant text-[11px] flex-shrink-0">
                {Math.round(f.size / 1024)}KB
              </span>
              <button
                type="button"
                aria-label={`Remove ${f.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  removeAt(i);
                }}
                disabled={disabled}
                className="text-on-surface-variant hover:text-error flex-shrink-0"
              >
                <X className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {shownError && (
        <p role="alert" className="text-error text-body-sm mt-1">
          {shownError}
        </p>
      )}
    </div>
  );
}
