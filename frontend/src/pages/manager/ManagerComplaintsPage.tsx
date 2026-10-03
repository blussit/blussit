import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { complaintApi } from "../../api/engagement";
import { Badge, DataTable, StatusBadge } from "../../components/ui";
import { ComplaintDetailDrawer } from "../../components/shared/ComplaintDetailDrawer";
import { normalisePhoneSearch, Pager, SearchBox, useDebouncedValue } from "../../components/shared/ListControls";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";
import { toTitle } from "../../lib/titleCase";

const PRIORITY_TONE: Record<string, "error" | "warning" | "neutral"> = { urgent: "error", high: "error", medium: "warning", low: "neutral" };
const PAGE_SIZE = 20;

const STATUS_TABS = [
  { key: "", label: "All" },
  { key: "open", label: "Open" },
  { key: "in_progress", label: "In Progress" },
  { key: "resolved", label: "Resolved" },
  { key: "closed", label: "Closed" },
];

/**
 * Every complaint here is already scoped to this manager's own service
 * center by the backend (complaints inherit service_center_id from the
 * booking they're about, and list_for_center enforces ensure_own_center) —
 * so what a manager sees is exactly "complaints about my center's
 * bookings", never anyone else's. Filter and search run on the server;
 * clicking a row opens the full detail + reply thread.
 */
export default function ManagerComplaintsPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const [status, setStatus] = useState("open");
  const [category, setCategory] = useState<"" | "society">("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Complaint | null>(null);
  const debouncedSearch = useDebouncedValue(normalisePhoneSearch(search), 300);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["center-complaints-page", centerId, status, category, debouncedSearch, page],
    queryFn: () =>
      complaintApi.forCenter(centerId, { status: status || undefined, category: category || undefined, search: debouncedSearch || undefined, page, page_size: PAGE_SIZE }),
    enabled: !!centerId,
    placeholderData: keepPreviousData,
  });

  // The open complaint is its own copy — refreshed whenever the list
  // reloads with it, updated straight from a save — so a status change that
  // drops it out of the current filter doesn't yank the drawer shut.
  useEffect(() => {
    setSelected((cur) => (cur ? (data?.data || []).find((c) => c.id === cur.id) || cur : cur));
  }, [data]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Complaints</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Resolve customer issues for your service center — click a complaint for the full thread.</p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex flex-wrap gap-1.5">
          {STATUS_TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => {
                setStatus(t.key);
                setPage(1);
              }}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${
                status === t.key ? "border border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-gray-200 text-gray-500 hover:border-gray-300"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => {
            setCategory((c) => (c ? "" : "society"));
            setPage(1);
          }}
          aria-pressed={category === "society"}
          data-testid="filter-society-issues"
          className={`rounded-full border px-3 py-1 text-xs font-medium ${
            category === "society" ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-gray-200 text-gray-500 hover:border-gray-300"
          }`}
        >
          Society Issues
        </button>
        <SearchBox
          className="sm:ml-auto sm:w-80"
          value={search}
          onChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          placeholder="Subject, booking no., name or phone"
        />
      </div>

      <DataTable<Complaint>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle={status === "open" && !debouncedSearch ? "No Open Complaints" : "No Complaints Match"}
        onRowClick={(c) => setSelected(c)}
        columns={[
          { header: "Subject", accessor: (c) => c.subject },
          { header: "Customer", accessor: (c) => c.customer_name || "—" },
          { header: "Booking", accessor: (c) => (c.category === "society"
            ? <span className="text-xs font-semibold text-[#0A66F0]">Society · {c.society_name}{c.registration_number ? ` · ${c.registration_number}` : ""}</span>
            : <span className="font-mono-num">{c.booking_number || "—"}</span>) },
          { header: "Priority", accessor: (c) => <Badge tone={PRIORITY_TONE[c.priority] || "neutral"}>{toTitle(c.priority)}</Badge> },
          { header: "Raised", accessor: (c) => format(c.created_at) },
          { header: "Status", accessor: (c) => <StatusBadge status={c.status} /> },
        ]}
      />

      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}

      <ComplaintDetailDrawer complaint={selected} onClose={() => setSelected(null)} onUpdated={setSelected} />
    </div>
  );
}
