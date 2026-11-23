/** Exact rupees for documents and invoice previews: whole rupees get no decimals, paise show as ".05". */
export function rupeesExact(paise?: number | null): string {
  const p = Math.round(Number(paise || 0));
  const abs = Math.abs(p);
  const rupees = Math.floor(abs / 100);
  const rest = abs % 100;
  const body = `₹${rupees.toLocaleString("en-IN")}${rest ? `.${String(rest).padStart(2, "0")}` : ""}`;
  return p < 0 ? `-${body}` : body;
}
