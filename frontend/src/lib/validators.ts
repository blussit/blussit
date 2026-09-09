/**
 * Client-side mirrors of the backend's Indian plate/phone validation
 * (app/utils/vehicle_reg.py, app/utils/phone.py). The server remains the
 * real guard — these exist so people get instant, friendly feedback
 * instead of a submit-and-bounce.
 */
const STATE_CODES = new Set([
  "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ",
  "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP",
  "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TN", "TR", "TS", "TG",
  "UK", "UA", "UP", "WB",
]);

export function normalizePlate(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

/** Returns the canonical plate (e.g. "MP09AB1234") or null. Accepts the
 * standard state series (incl. Delhi-style DL1CXY9876 and old no-letter
 * plates) and the Bharat series (22BH1234AB). */
export function validateIndianPlate(raw: string): string | null {
  const plate = normalizePlate(raw);
  if (plate.length < 6 || plate.length > 11) return null;
  const std = plate.match(/^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})$/);
  if (std && STATE_CODES.has(std[1])) return plate;
  if (/^\d{2}BH\d{4}[A-Z]{1,2}$/.test(plate)) return plate;
  return null;
}

export const PLATE_FORMAT_HINT = "e.g. MP09AB1234, DL1CXY9876 or 22BH1234AB";

/** Returns the canonical bare 10-digit mobile or null. Accepts +91/91/0
 * prefixes and any spacing; Indian mobiles start with 6-9. */
export function validateIndianMobile(raw: string): string | null {
  let digits = (raw || "").replace(/\D/g, "");
  if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
  else if (digits.length === 11 && digits.startsWith("0")) digits = digits.slice(1);
  return digits.length === 10 && "6789".includes(digits[0]) ? digits : null;
}
