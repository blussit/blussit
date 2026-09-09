/** Small −/+ counter used for per-bike add-on quantities in both booking flows. */
export function QtyStepper({ value, min, max, onChange }: { value: number; min: number; max: number; onChange: (n: number) => void }) {
  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={() => onChange(Math.max(min, value - 1))}
        disabled={value <= min}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-gray-300 text-sm font-bold text-[var(--color-text-primary)] disabled:opacity-30"
        aria-label="Decrease"
      >
        −
      </button>
      <span className="w-5 text-center font-mono-num text-sm font-bold text-[var(--color-text-primary)]">{value}</span>
      <button
        type="button"
        onClick={() => onChange(Math.min(max, value + 1))}
        disabled={value >= max}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-gray-300 text-sm font-bold text-[var(--color-text-primary)] disabled:opacity-30"
        aria-label="Increase"
      >
        +
      </button>
    </span>
  );
}
