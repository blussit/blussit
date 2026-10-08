/** Small −/+ counter used for per-bike add-on quantities in both booking flows.
 *  `size="lg"` (the customer sheets) gives 44px taps; the default is unchanged. */
export function QtyStepper({
  value,
  min,
  max,
  onChange,
  size = "sm",
  label,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (n: number) => void;
  size?: "sm" | "lg";
  /** What is being counted, for screen readers ("Bikes"). */
  label?: string;
}) {
  const btnSize = size === "lg" ? "h-11 w-11 text-base" : "h-7 w-7 text-sm";
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={() => onChange(Math.max(min, value - 1))}
        disabled={value <= min}
        className={`flex ${btnSize} items-center justify-center rounded-full border border-gray-300 font-bold text-[var(--color-text-primary)] disabled:opacity-30`}
        aria-label={label ? `Fewer ${label}` : "Decrease"}
      >
        −
      </button>
      <span className="w-5 text-center font-mono-num text-sm font-bold text-[var(--color-text-primary)]">{value}</span>
      <button
        type="button"
        onClick={() => onChange(Math.min(max, value + 1))}
        disabled={value >= max}
        className={`flex ${btnSize} items-center justify-center rounded-full border border-gray-300 font-bold text-[var(--color-text-primary)] disabled:opacity-30`}
        aria-label={label ? `More ${label}` : "Increase"}
      >
        +
      </button>
    </span>
  );
}
