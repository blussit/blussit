/**
 * Money for the payroll screens. The server stores and sends integer PAISE;
 * people read and type whole rupees. Conversion happens only here, at the
 * edges: `rupeesText` for display, `toPaise` for what a person typed.
 */

/** 12500000 -> "₹1,25,000" (en-IN grouping, whole rupees). A negative sign leads: "-₹3,000". */
export function rupeesText(paise?: number | null): string {
  const rupees = Math.round(Number(paise || 0) / 100);
  const body = `₹${Math.abs(rupees).toLocaleString("en-IN")}`;
  return rupees < 0 ? `-${body}` : body;
}

/** What a person typed ("15,000", "₹ 15000") -> paise, or null when it isn't a whole-rupee number >= 0. */
export function toPaise(input: string | number | null | undefined): number | null {
  if (input === null || input === undefined) return null;
  const text = String(input).replace(/[₹,\s]/g, "").replace(/\.$/, "");
  if (!/^\d+$/.test(text)) return null;
  const rupees = Number(text);
  return Number.isSafeInteger(rupees) ? rupees * 100 : null;
}

/** Paise -> a plain whole-rupee string for an input field ("15000"); "" for 0/none. */
export function rupeesInput(paise?: number | null): string {
  const rupees = Math.round(Number(paise || 0) / 100);
  return rupees ? String(rupees) : "";
}

/** Digits only, for an amount input. */
export const cleanAmountInput = (raw: string) => {
  const whole = raw.split(".")[0].replace(/\D/g, "").slice(0, 9);
  // A just-typed "." stays on screen until the next key, so "30.5" is read as 30 — never 305.
  return raw.includes(".") && raw.indexOf(".") === raw.length - 1 && whole ? `${whole}.` : whole;
};

const ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"];
const TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"];

function belowThousand(n: number): string {
  const parts: string[] = [];
  if (n >= 100) {
    parts.push(`${ONES[Math.floor(n / 100)]} Hundred`);
    n %= 100;
  }
  if (n >= 20) {
    parts.push(TENS[Math.floor(n / 10)] + (n % 10 ? ` ${ONES[n % 10]}` : ""));
  } else if (n > 0) {
    parts.push(ONES[n]);
  }
  return parts.join(" ");
}

/** 125000 -> "One Lakh Twenty Five Thousand Rupees" (Indian numbering). */
export function rupeesInWords(rupees: number): string {
  let n = Math.max(0, Math.round(rupees));
  if (n === 0) return "Zero Rupees";
  const units: [number, string][] = [
    [10_000_000, "Crore"],
    [100_000, "Lakh"],
    [1_000, "Thousand"],
  ];
  const parts: string[] = [];
  for (const [size, name] of units) {
    if (n >= size) {
      parts.push(`${belowThousand(Math.floor(n / size))} ${name}`);
      n %= size;
    }
  }
  if (n > 0) parts.push(belowThousand(n));
  return `${parts.join(" ")} Rupees`;
}
