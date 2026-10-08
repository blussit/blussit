import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { complaintApi } from "../../api/engagement";
import { adminServiceCenterApi } from "../../api/admin";
import { Badge, DataTable, Select, StatusBadge } from "../../components/ui";
import { ComplaintDetailDrawer } from "../../components/shared/ComplaintDetailDrawer";
import { normalisePhoneSearch, Pager, SearchBox, useDebouncedValue } from "../../components/shared/ListControls";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";
import { toTitle } from "../../lib/titleCase";

const PRIORITY_TONE: Record<string, "error" | "warning" | "neutral"> = { urgent: "error", high: "error", medium: "warning", low: "neutral" };
const PAGE_SIZE = 25;

/**
 * Platform-wide complaint oversight — every complaint, from every service
 * center, filterable by center and status and searchable (subject, booking
 * number, customer name/phone) on the server. Clicking a row opens the same
 * full detail + reply thread a manager sees, so admin can jump in on any
 * complaint directly rather than only watching from the sidelines.
 */
export default function AdminComplaintsPage() {
  const [status, setStatus] = useState("");
  const [centerFilter, setCenterFilter] = useState("");
  const [category, setCategory] = useState<"" | "society" | "booking">("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Complaint | null>(null);
  const debouncedSearch = useDebouncedValue(normalisePhoneSearch(search), 300);

  const { data: centers } = useQuery({ queryKey: ["admin-centers-for-complaints"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["admin-complaints", status, centerFilter, category, debouncedSearch, page],
    queryFn: () =>
      complaintApi.all({
        status: status || undefined,
        service_center_id: centerFilter || undefined,
        category: category || undefined,
        search: debouncedSearch || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
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
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Platform-wide complaint oversight — click a complaint for the full thread.</p>
      </div>

      <div className="grid grid-cols-1 items-end gap-3 sm:flex sm:flex-wrap">
        <SearchBox
          className="sm:min-w-[260px] sm:max-w-md sm:flex-1"
          value={search}
          onChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          placeholder="Subject, booking no., customer name or phone"
        />
        <div className="sm:max-w-xs">
          <Select
            label="Filter By Status"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All Statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In Progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </Select>
        </div>
        <div className="sm:max-w-xs">
          <Select
            label="Filter By Service Center"
            value={centerFilter}
            onChange={(e) => {
              setCenterFilter(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All Service Centers</option>
            {(centers?.data || []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="sm:max-w-xs">
          <Select
            label="Type"
            value={category}
            onChange={(e) => {
              setCategory(e.target.value as "" | "society" | "booking");
              setPage(1);
            }}
          >
            <option value="">All Complaints</option>
            <option value="booking">Booking Complaints</option>
            <option value="society">Society Issues</option>
          </Select>
        </div>
      </div>

      <DataTable<Complaint>
        isLoading={isLoading}
        data={data?.data || []}
        error={error}
        onRetry={() => void refetch()}
        emptyTitle={debouncedSearch || status || centerFilter ? "No Complaints Match" : "No Complaints"}
        onRowClick={(c) => setSelected(c)}
        columns={[
          { header: "Subject", accessor: (c) => c.subject },
          { header: "Customer", accessor: (c) => c.customer_name || "—" },
          { header: "Booking", accessor: (c) => (c.category === "society"
            ? <span className="text-xs font-semibold text-[#0A66F0]">Society · {c.society_name}{c.registration_number ? ` · ${c.registration_number}` : ""}</span>
            : <span className="font-mono-num">{c.booking_number || "—"}</span>) },
          { header: "Service Center", accessor: (c) => c.service_center_name || "—" },
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
