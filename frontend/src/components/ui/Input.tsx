import { forwardRef, type ChangeEvent, type InputHTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { FIELD_ERROR, FIELD_HINT, FIELD_LABEL, fieldBox } from "./fieldStyles";
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

  // Same v2 field box as Select/DatePicker/TimePicker (ui/fieldStyles), so a
  // text field and a dropdown side by side line up and look like one set.
  // min-w-0: a text box in a flex row may shrink instead of pushing the
  // row past a 320 px screen (an <input>'s intrinsic width is ~170 px).
  return (
    <div className="w-full min-w-0">
      {label && (
        <label htmlFor={inputId} className={FIELD_LABEL}>
          {label}
        </label>
      )}
      <input
        ref={ref}
        id={inputId}
        type={type}
        aria-invalid={error ? true : undefined}
        className={cn(fieldBox({ error, disabled: props.disabled }), "placeholder:text-[#9AA3B2]", className)}
        {...props}
      />
      {hint && !error && <p className={FIELD_HINT}>{hint}</p>}
      {error && <p className={FIELD_ERROR}>{error}</p>}
    </div>
  );
});
Input.displayName = "Input";
