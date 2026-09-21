import { cn } from "../../lib/cn";

/**
 * A labelled on/off row. Same selection language as the booking cards:
 * light tint + black border when on, plain white when off; the track is
 * the brand gold.
 */
export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "flex w-full items-center justify-between gap-4 rounded-xl border-2 p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        checked ? "border-black bg-[#FFF4CD]" : "border-gray-200 bg-white hover:border-gray-400"
      )}
    >
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-black">{label}</span>
        {description && <span className="block text-xs text-gray-500">{description}</span>}
      </span>
      <span aria-hidden className={cn("relative h-6 w-11 shrink-0 rounded-full transition-colors", checked ? "bg-[#E8A900]" : "bg-gray-300")}>
        <span className={cn("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform", checked ? "translate-x-[22px]" : "translate-x-0.5")} />
      </span>
    </button>
  );
}
