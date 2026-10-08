import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Plus, Search } from "lucide-react";
import { Button, DataTable, Input, Select, StatCard } from "../ui";
import { adminServiceCenterApi } from "../../api/admin";
import { rupees, societyApi, type SocietyInput, type SocietyRow } from "../../api/society";
import { SocietyFormModal } from "./SocietyFormModal";
import { SocietyRequestsPanel } from "./SocietyRequestsPanel";

/** Societies list: KPIs + one row per society. Manager = own center
 * (server-enforced); admin can filter by center. */
export function SocietyListView({ basePath, isAdmin = false }: { basePath: string; isAdmin?: boolean }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [centerId, setCenterId] = useState("");
  const [open, setOpen] = useState(false);
  const [prefill, setPrefill] = useState<Partial<SocietyInput> | null>(null);
  const list = useQuery({
    queryKey: ["societies", centerId, search],
    queryFn: () => societyApi.list({ center_id: centerId || undefined, search: search.trim() || undefined }),
  });
  const centers = useQuery({ queryKey: ["admin-centers-all"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }), enabled: isAdmin });
  const k = list.data?.kpis;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold text-black">Societies</h1>
          <p className="text-sm text-gray-500">Daily bucket wash + premium washes, per car.</p>
        </div>
        <Button onClick={() => { setPrefill(null); setOpen(true); }}><Plus className="h-4 w-4" /> Register Society</Button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="Societies" value={k?.societies ?? "—"} icon={Building2} />
        <StatCard label="Residents" value={k?.residents ?? "—"} hint="with an active plan" />
        <StatCard label="Active Cars" value={k?.active_cars ?? "—"} />
        <StatCard label="Requests" value={k?.requests ?? "—"} tone={k?.requests ? "warning" : "default"} hint="waiting for you" />
        <StatCard label="Revenue This Month" value={k ? rupees(k.revenue_month) : "—"} hint={k ? `${k.attended_today} attended today` : undefined} />
      </div>

      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name, area, pincode" aria-label="Search societies" />
        </div>
        {isAdmin && (
          <div className="w-full sm:w-64">
            <Select aria-label="Center" value={centerId} onChange={(e) => setCenterId(e.target.value)}>
              <option value="">All Centers</option>
              {(centers.data?.data || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
          </div>
        )}
      </div>

      <DataTable<SocietyRow>
        data={list.data?.rows || []}
        isLoading={list.isLoading}
        error={list.error}
        onRetry={() => void list.refetch()}
        emptyTitle="No Societies Yet"
        emptyDescription="Register one, then share its form link with the residents."
        onRowClick={(r) => navigate(`${basePath}/${r.id}`)}
        columns={[
          { header: "Society", accessor: (r) => <div><p className="font-semibold text-black">{r.name}</p><p className="text-xs text-gray-500">{[r.area, r.city, r.pincode].filter(Boolean).join(", ")}</p></div> },
          ...(isAdmin ? [{ header: "Center", accessor: (r: SocietyRow) => r.service_center_name || "—" }] : []),
          { header: "Residents", accessor: (r) => r.residents },
          { header: "Cars", accessor: (r) => r.active_cars },
          { header: "Requests", accessor: (r) => (r.requests ? <span className="font-semibold text-amber-600">{r.requests}</span> : 0) },
          { header: "Open Issues", accessor: (r) => (r.open_issues ? <span className="font-semibold text-amber-600">{r.open_issues}</span> : 0) },
          { header: "Captain Today", accessor: (r) => <span>{r.today_captain_name || "—"}{r.attended_today && <span className="ml-1 text-xs text-green-700">· Present</span>}</span> },
          { header: "Days This Month", accessor: (r) => r.attendance_days_month },
          { header: "Revenue", accessor: (r) => rupees(r.revenue_month) },
        ]}
      />

      <SocietyRequestsPanel isAdmin={isAdmin} onRegister={(p) => { setPrefill(p); setOpen(true); }} />

      <SocietyFormModal open={open} onClose={() => setOpen(false)} isAdmin={isAdmin} prefill={prefill}
        onSaved={(s) => {
          setOpen(false);
          queryClient.invalidateQueries({ queryKey: ["societies"] });
          queryClient.invalidateQueries({ queryKey: ["society-leads"] });
          navigate(`${basePath}/${s.id}`);
        }} />
    </div>
  );
}
