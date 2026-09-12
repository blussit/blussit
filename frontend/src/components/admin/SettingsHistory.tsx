import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, History } from "lucide-react";
import { adminSettingsApi } from "../../api/admin";
import { formatDateTime } from "../../lib/date";

/**
 * "Last changed 12 Sept 2026, 2:14 pm by Mukul" under an admin setting,
 * with the full change log one tap away — each entry naming the fields
 * that moved and their before → after. Read straight from the
 * settings_history rows the backend writes on every real save.
 */
export function SettingsHistory({ settingKey, labels = {} }: { settingKey: string; labels?: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({ queryKey: ["settings-history", settingKey], queryFn: () => adminSettingsApi.history(settingKey) });
  const latest = data?.[0];
  const label = (field: string) => labels[field] || field.replace(/_/g, " ");
  const show = (v: unknown) => (v == null || v === "" ? "—" : typeof v === "boolean" ? (v ? "on" : "off") : String(v));

  return (
    <div className="mt-3 text-xs text-[var(--color-text-secondary)]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="inline-flex items-center gap-1.5">
          <History className="h-3.5 w-3.5" />
          {latest ? (
            <>
              Last changed <span className="font-medium text-[var(--color-text-primary)]">{formatDateTime(latest.changed_at)}</span> by{" "}
              <span className="font-medium text-[var(--color-text-primary)]">{latest.changed_by_name}</span>
            </>
          ) : (
            "No changes recorded yet — defaults in use."
          )}
        </span>
        {!!data?.length && (
          <button type="button" onClick={() => setOpen((v) => !v)} className="inline-flex items-center gap-1 font-semibold text-black underline underline-offset-2">
            {open ? "Hide history" : `Show history (${data.length})`}
            <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        )}
      </div>
      {open && !!data?.length && (
        <ol className="mt-2 space-y-2 border-l-2 border-[#F3E5B5] pl-3">
          {data.map((entry) => (
            <li key={entry.changed_at}>
              <p className="font-medium text-[var(--color-text-primary)]">
                {formatDateTime(entry.changed_at)} · {entry.changed_by_name}
              </p>
              <ul className="mt-0.5 space-y-0.5">
                {Object.entries(entry.changes).map(([field, change]) => (
                  <li key={field}>
                    <span className="capitalize">{label(field)}</span>: <span className="font-mono-num line-through decoration-gray-400">{show(change.from)}</span>{" "}
                    → <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{show(change.to)}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
