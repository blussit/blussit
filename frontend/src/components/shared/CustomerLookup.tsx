import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, UserRound } from "lucide-react";
import { crmApi } from "../../api/crm";
import { CustomerDetailDrawer } from "./CustomerDetailDrawer";
import { normalisePhoneSearch, useDebouncedValue } from "./ListControls";

/**
 * Header search for managers and admins: type a name or number, pick the
 * customer, and their bookings + plans open in the customer drawer. Server
 * search-as-you-type (a few rows per query) — never a list of all users.
 */
export function CustomerLookup() {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [customerId, setCustomerId] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const query = useDebouncedValue(normalisePhoneSearch(text), 300);

  const { data: results = [], isFetching } = useQuery({
    queryKey: ["customer-lookup", query],
    queryFn: () => crmApi.customerTypeahead(query, 10),
    enabled: query.length >= 2,
    staleTime: 30_000,
  });

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const showList = open && query.length >= 2;

  return (
    <>
      <div ref={boxRef} className="relative w-36 sm:w-64">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          type="search"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder="Find customer"
          aria-label="Find a customer by name or phone"
          className="w-full rounded-full border border-[#F3E5B5] bg-white py-1.5 pl-9 pr-3 text-sm text-black placeholder:text-gray-400 focus:border-black focus:outline-none"
        />
        {showList && (
          <div className="absolute right-0 top-full z-40 mt-1.5 w-[min(18rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[#F3E5B5] bg-white shadow-[0_16px_40px_-12px_rgba(0,0,0,0.25)]">
            {results.length === 0 ? (
              <p className="px-3.5 py-3 text-sm text-gray-500">{isFetching ? "Searching…" : "No customer matches."}</p>
            ) : (
              results.map((u) => (
                <button
                  key={u.id}
                  onClick={() => {
                    setCustomerId(u.id);
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-[#FFFCF0]"
                >
                  <UserRound className="h-4 w-4 shrink-0 text-gray-400" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-black">{u.full_name || "Customer"}</span>
                    <span className="block truncate text-xs text-gray-500">{u.phone || u.email || "—"}</span>
                  </span>
                </button>
              ))
            )}
          </div>
        )}
      </div>
      <CustomerDetailDrawer customerId={customerId} onClose={() => setCustomerId(null)} />
    </>
  );
}
