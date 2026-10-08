import { useEffect, useRef } from "react";
import { cn } from "../../lib/cn";

/**
 * Six separate digit boxes, one per character — the pattern every Indian
 * customer already knows from UPI and banking apps. Handles the things a
 * naive six-input implementation gets wrong:
 *  - paste of the whole code (or an SMS autofill) fills every box;
 *  - Backspace on an empty box steps back and clears the previous one;
 *  - arrow keys move between boxes; typing over a filled box replaces it;
 *  - only digits, and the native numeric keypad on phones;
 *  - the browser/OS one-time-code autofill still works (autoComplete on
 *    the first box, and no maxLength — iOS truncates an autofilled code to
 *    maxLength, which with 1 left only the first digit);
 *  - a pasted SMS ("Your code 482913. Valid 10 min") yields the code, not
 *    every digit in the message.
 */

/** The code inside pasted/autofilled text: a standalone run of exactly
 * `length` digits wins over "all the digits in the message". */
function extractCode(text: string, length: number): string {
  // No lookbehind: older iOS Safari throws on it.
  const run = text.match(new RegExp(`(?:^|\\D)(\\d{${length}})(?!\\d)`));
  return (run ? run[1] : text.replace(/\D/g, "")).slice(0, length);
}

export function OtpInput({
  value,
  onChange,
  length = 6,
  autoFocus = false,
  disabled = false,
  onComplete,
}: {
  value: string;
  onChange: (v: string) => void;
  length?: number;
  autoFocus?: boolean;
  disabled?: boolean;
  /** Fired once the last digit lands — lets callers auto-submit. */
  onComplete?: (v: string) => void;
}) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const digits = value.padEnd(length, " ").slice(0, length).split("");

  // Also on a cleared value once re-enabled (a rejected code is wiped) —
  // the disabled boxes lost focus while it was being checked.
  const cleared = value === "";
  useEffect(() => {
    if (autoFocus && cleared && !disabled) refs.current[0]?.focus();
  }, [autoFocus, cleared, disabled]);

  const commit = (next: string) => {
    const clean = next.replace(/\D/g, "").slice(0, length);
    onChange(clean);
    if (clean.length === length) onComplete?.(clean);
    return clean;
  };

  const handleChange = (index: number, raw: string) => {
    let typed = raw.replace(/\D/g, "");
    if (!typed) return;
    // Typing into a box that already holds a digit (caret after it, not
    // selected) arrives as two chars — keep only the new one.
    const prev = digits[index]?.trim();
    if (typed.length === 2 && prev && (typed.startsWith(prev) || typed.endsWith(prev))) {
      typed = typed.startsWith(prev) ? typed.slice(1) : typed.slice(0, 1);
    }
    // Pasting/autofilling the whole code into any box fills the rest.
    if (typed.length > 1) {
      const clean = commit(extractCode(raw, length));
      refs.current[Math.min(clean.length, length - 1)]?.focus();
      return;
    }
    const chars = value.padEnd(length, " ").split("");
    chars[index] = typed;
    commit(chars.join("").replace(/\s/g, " ").trimEnd());
    if (index < length - 1) refs.current[index + 1]?.focus();
  };

  const handleKeyDown = (index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace") {
      e.preventDefault();
      const chars = value.padEnd(length, " ").split("");
      if (chars[index] && chars[index] !== " ") {
        chars[index] = " ";
        commit(chars.join("").trimEnd());
      } else if (index > 0) {
        chars[index - 1] = " ";
        commit(chars.join("").trimEnd());
        refs.current[index - 1]?.focus();
      }
      return;
    }
    if (e.key === "ArrowLeft" && index > 0) refs.current[index - 1]?.focus();
    if (e.key === "ArrowRight" && index < length - 1) refs.current[index + 1]?.focus();
  };

  return (
    <div className="flex justify-between gap-2" role="group" aria-label="Verification code">
      {Array.from({ length }).map((_, i) => (
        <input
          key={i}
          ref={(el) => {
            refs.current[i] = el;
          }}
          value={digits[i]?.trim() || ""}
          onChange={(e) => handleChange(i, e.target.value)}
          onKeyDown={(e) => handleKeyDown(i, e)}
          onFocus={(e) => e.target.select()}
          onPaste={(e) => {
            e.preventDefault();
            const clean = commit(extractCode(e.clipboardData.getData("text"), length));
            refs.current[Math.min(clean.length, length - 1)]?.focus();
          }}
          inputMode="numeric"
          autoComplete={i === 0 ? "one-time-code" : "off"}
          aria-label={`Digit ${i + 1}`}
          disabled={disabled}
          className={cn(
            "font-mono-num h-12 w-full min-w-0 rounded-xl border text-center text-lg font-bold text-[#0E1A33] transition-colors",
            "focus:border-[#0A66F0] focus:outline-none focus:ring-2 focus:ring-[#0A66F0]/15",
            "disabled:cursor-not-allowed disabled:opacity-50",
            digits[i]?.trim() ? "border-[#0A66F0] bg-white" : "border-[#E4E9F1] bg-white"
          )}
        />
      ))}
    </div>
  );
}
