import { useEffect, useState } from "react";
import { crmApi } from "../api/crm";
import type { User } from "../types";

/** Debounced existing-customer lookup by name or phone, for manager forms
 * where typing a name/number should surface a matching customer to pick
 * directly instead of risking a duplicate account. Pass "" to disable. */
export function useCustomerTypeahead(query: string): User[] {
  const [results, setResults] = useState<User[]>([]);

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      crmApi
        .customerTypeahead(query.trim())
        .then((data) => {
          if (!cancelled) setResults(data || []);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  return results;
}
