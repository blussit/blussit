import { forwardRef, type ChangeEvent, type InputHTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { DatePicker } from "./DatePicker";
import { TimePicker } from "./TimePicker";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  hint?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(({ className, label, error, hint, id, type, ...props }, ref) => {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, "-");

  // Native <input type="date"> hands its calendar popup to the OS/webview shell,
  // same story as the native <select> fix in ui/Select.tsx — it can render
  // detached from the field and off-panel. Route date inputs through the
  // in-DOM, theme-matched DatePicker instead; every other <input> type is
  // untouched. onChange is bridged to the same (event with e.target.value)
  // contract every call site already uses, so no callers need to change.
  if (type === "date") {
    const { value, onChange, min, max, required, disabled, placeholder } = props;
    return (
      <div className="w-full">
        <DatePicker
          label={label}
          error={error}
          id={inputId}
          value={typeof value === "string" ? value : undefined}
          min={typeof min === "string" ? min : undefined}
          max={typeof max === "string" ? max : undefined}
          required={required}
          disabled={disabled}
          placeholder={typeof placeholder === "string" ? placeholder : undefined}
          className={className}
          onChange={(v) => onChange?.({ target: { value: v }, currentTarget: { value: v } } as unknown as ChangeEvent<HTMLInputElement>)}
        />
        {hint && !error && <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{hint}</p>}
      </div>
    );
  }

  // Same story as type="date" above — native <input type="time"> hands its
  // picker to the OS/webview shell too, which is what let it render
  // detached and cut off the visible panel. min/max here follow the same
  // "HH:MM" contract native <input type="time" min max> already uses —
  // pass a service center's working hours to bound the picker to them.
  if (type === "time") {
    const { value, onChange, min, max, required, disabled, placeholder } = props;
    return (
      <TimePicker
        label={label}
        error={error}
        id={inputId}
        value={typeof value === "string" ? value : undefined}
        minTime={typeof min === "string" ? min : undefined}
        maxTime={typeof max === "string" ? max : undefined}
        required={required}
        disabled={disabled}
        placeholder={typeof placeholder === "string" ? placeholder : undefined}
        className={className}
        onChange={(v) => onChange?.({ target: { value: v }, currentTarget: { value: v } } as unknown as ChangeEvent<HTMLInputElement>)}
      />
    );
  }

  return (
    <div className="w-full">
      {label && (
        <label htmlFor={inputId} className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
          {label}
        </label>
      )}
      <input
        ref={ref}
        id={inputId}
        type={type}
        className={cn(
          "w-full rounded-xl border bg-white px-3.5 py-2.5 text-sm text-[var(--color-text-primary)] placeholder:text-gray-400 transition-colors",
          "focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]",
          error ? "border-[var(--color-error)]" : "border-gray-300",
          className
        )}
        {...props}
      />
      {hint && !error && <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{hint}</p>}
      {error && <p className="mt-1 text-xs text-[var(--color-error)]">{error}</p>}
    </div>
  );
});
Input.displayName = "Input";
