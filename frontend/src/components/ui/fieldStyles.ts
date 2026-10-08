import { cn } from "../../lib/cn";

/**
 * The v2 form-field look, in one place, so every field and dropdown in the
 * app (Input, Select, DatePicker, TimePicker, and the few custom menus) reads
 * as the same control: white box, #E4E9F1 hairline, 12px radius, 44px tall
 * (40 compact), navy #0E1A33 text, muted #5F6878 chrome, #C9D6EA hover and
 * a blue #0A66F0 focus border with a soft 15% ring. Disabled = #F5F7FA fill.
 *
 * `cn` is plain clsx (no tailwind-merge), so two utilities for the same
 * property must never both be emitted — that's why the border colour is
 * picked by state here instead of being layered with overrides.
 */
export const FIELD_LABEL = "mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]";
export const FIELD_HINT = "mt-1 text-xs text-[var(--color-text-secondary)]";
export const FIELD_ERROR = "mt-1 text-xs text-[var(--color-error)]";
/** Chevron / calendar / clock glyphs inside a field. */
export const FIELD_ICON = "h-4 w-4 shrink-0 text-[#5F6878]";
/** Empty-state text inside a trigger ("Select date", an empty-value option). */
export const FIELD_PLACEHOLDER = "text-[#5F6878]";

export function fieldBox({
  error,
  open = false,
  disabled = false,
  compact = false,
}: {
  error?: string | boolean;
  open?: boolean;
  disabled?: boolean;
  compact?: boolean;
}): string {
  return cn(
    "w-full min-w-0 rounded-[12px] border bg-white px-3.5 text-sm text-[#0E1A33] transition-[border-color,box-shadow] duration-150",
    compact ? "h-10" : "h-11",
    "focus:outline-none focus-visible:outline-none",
    "disabled:cursor-not-allowed disabled:bg-[#F5F7FA] disabled:text-[#5F6878]",
    error
      ? "border-[var(--color-error)] focus:ring-[3px] focus:ring-[#DC2626]/15"
      : open
        ? "border-[#0A66F0] ring-[3px] ring-[#0A66F0]/15"
        : "border-[#E4E9F1] focus:border-[#0A66F0] focus:ring-[3px] focus:ring-[#0A66F0]/15",
    !error && !open && !disabled && "hover:border-[#C9D6EA]"
  );
}

/** Floating panel for dropdown lists, suggestion lists and pickers. */
export const MENU_PANEL =
  "rounded-[12px] border border-[#E4E9F1] bg-white shadow-[0_16px_36px_-14px_rgba(14,26,51,0.26),0_2px_6px_-2px_rgba(14,26,51,0.08)]";
/** One row in a menu panel; pair with exactly one of the state classes below. */
export const MENU_ITEM = "rounded-lg px-3 py-2 text-sm transition-colors";
export const MENU_ITEM_IDLE = "text-[#0E1A33] hover:bg-[#F3F6FA]";
export const MENU_ITEM_SELECTED = "bg-[#E8F0FE] font-medium text-[#0A66F0]";
export const MENU_ITEM_DISABLED = "cursor-not-allowed text-[#9AA3B2]";
