/** Paise → whole-rupee en-IN display ("₹1,25,000"); negatives keep their sign. */
export const inr = (paise?: number | null): string => {
  if (paise == null || Number.isNaN(paise)) return "—";
  const rupees = Math.round(Math.abs(paise) / 100);
  return `${paise < 0 && rupees > 0 ? "-" : ""}₹${rupees.toLocaleString("en-IN")}`;
};

/** A typed rupee amount ("1500" or "1500.50") → paise, or null when invalid. */
export function rupeesToPaise(text: string): number | null {
  if (!/^\d{1,9}(\.\d{1,2})?$/.test(text.trim())) return null;
  const paise = Math.round(Number(text) * 100);
  return paise > 0 ? paise : null;
}
