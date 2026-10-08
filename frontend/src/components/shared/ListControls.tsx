import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { Button } from "../ui";

/** `value`, but only after it has stopped changing for `ms` — so a search
 *  box fires one request per pause, not one per keystroke. */
export function useDebouncedValue<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/** A pasted "+91 98765 43210" should still find the stored 10-digit number;
 *  anything that isn't a phone number passes through trimmed. */
export function normalisePhoneSearch(raw: string): string {
  const text = raw.trim();
  if (!/^[+\d][\d\s+-]*$/.test(text)) return text;
  let digits = text.replace(/\D/g, "");
  if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
  return digits;
}

export function SearchBox({
  value,
  onChange,
  placeholder = "Search",
  className = "",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <label className={`relative block ${className}`}>
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-xl border border-[#F3E5B5] bg-white py-2.5 pl-9 pr-3 text-sm text-black placeholder:text-gray-400 focus:border-black focus:outline-none"
      />
    </label>
  );
}

/** Prev / next for a server-paginated list (meta from `paginated()`). */
export function Pager({
  page,
  totalPages,
  total,
  onPage,
  busy,
}: {
  page: number;
  totalPages: number;
  total?: number;
  onPage: (page: number) => void;
  busy?: boolean;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className="flex items-center justify-center gap-3 text-sm text-gray-500">
      <Button size="sm" variant="outline" disabled={page <= 1 || busy} onClick={() => onPage(page - 1)}>
        Previous
      </Button>
      <span className="font-mono-num">
        Page {page} Of {totalPages}
        {total != null ? ` · ${total} Total` : ""}
      </span>
      <Button size="sm" variant="outline" disabled={page >= totalPages || busy} onClick={() => onPage(page + 1)}>
        Next
      </Button>
    </div>
  );
}
