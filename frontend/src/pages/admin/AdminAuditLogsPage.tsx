import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { adminServiceCenterApi, auditLogApi, type AuditLogRow } from "../../api/admin";
import { Badge, DataTable, Modal, Select } from "../../components/ui";
import { Pager } from "../../components/shared/ListControls";
import { formatDateTime } from "../../lib/date";
import { toTitle } from "../../lib/titleCase";

const MODULES = [
  { key: "bookings", label: "Bookings" },
  { key: "complaints", label: "Complaints" },
  { key: "users", label: "Users" },
  { key: "service_centers", label: "Service Centers" },
  { key: "services", label: "Services" },
  { key: "subscription_plans", label: "Plans" },
  { key: "user_subscriptions", label: "Subscriptions" },
  { key: "payment_orders", label: "Payments" },
  { key: "coupons", label: "Coupons" },
  { key: "settings", label: "Settings" },
  { key: "auth", label: "Sign-In" },
] as const;

/** "ASSIGN_CAPTAIN_GROUP" -> "Assign Captain Group". */
function actionLabel(code: string): string {
  return toTitle(code.toLowerCase());
}

/**
 * The audit trail as a trail: who did what, where, when. "Admin in a
 * center's queue" isolates what admins did while working a center as its
 * manager (AdminBookingsPage → Manage) — the server attributes each such
 * action to the center the record belongs to, never to anything the browser
 * sent.
 */
export default function AdminAuditLogsPage() {
  const [page, setPage] = useState(1);
  const [centerId, setCenterId] = useState("");
  const [module, setModule] = useState("");
  const [role, setRole] = useState("");
  const [adminInCenter, setAdminInCenter] = useState(false);
  const [open, setOpen] = useState<AuditLogRow | null>(null);
  useEffect(() => setPage(1), [centerId, module, role, adminInCenter]);

  const { data: centers } = useQuery({ queryKey: ["admin-centers-lite"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["admin-audit-logs", page, centerId, module, role, adminInCenter],
    queryFn: () =>
      auditLogApi.list({
        page,
        page_size: 25,
        service_center_id: centerId || undefined,
        module: module || undefined,
        actor_role: role || undefined,
        admin_in_center: adminInCenter || undefined,
      }),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Audit Logs</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Every sensitive admin and manager action — who, what, where and when.</p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:flex lg:flex-wrap lg:items-end">
        <div className="lg:w-60">
          <Select label="Service Center" compact value={centerId} onChange={(e) => setCenterId(e.target.value)}>
            <option value="">All Centers</option>
            {(centers?.data || []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="lg:w-48">
          <Select label="Area" compact value={module} onChange={(e) => setModule(e.target.value)}>
            <option value="">Everything</option>
            {MODULES.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </Select>
        </div>
        <div className="lg:w-40">
          <Select label="Done By" compact value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">Anyone</option>
            <option value="admin">Admins</option>
            <option value="manager">Managers</option>
            <option value="captain">Captains</option>
            <option value="customer">Customers</option>
          </Select>
        </div>
        <label className="flex h-10 cursor-pointer items-center gap-2 rounded-[12px] border border-[#E4E9F1] bg-white px-3.5 text-sm text-[var(--color-text-primary)] sm:col-span-2 lg:col-span-1">
          <input type="checkbox" checked={adminInCenter} onChange={(e) => setAdminInCenter(e.target.checked)} />
          Admin Actions In A Center's Queue
        </label>
      </div>

      <div className={isFetching && !isLoading ? "opacity-60 transition-opacity" : "transition-opacity"}>
        <DataTable<AuditLogRow>
          isLoading={isLoading}
          data={data?.data || []}
          error={error}
          onRetry={() => void refetch()}
          emptyTitle="No Matching Activity"
          emptyDescription="Try a wider filter — the trail keeps every action."
          onRowClick={setOpen}
          columns={[
            { header: "When", accessor: (a) => <span className="whitespace-nowrap text-xs text-gray-500">{formatDateTime(a.created_at)}</span> },
            {
              header: "Who",
              accessor: (a) => (
                <span className="block min-w-0">
                  <span className="block truncate font-medium">{a.actor_name || "—"}</span>
                  <span className="text-xs capitalize text-gray-500">{a.actor_role}</span>
                </span>
              ),
            },
            {
              header: "Action",
              accessor: (a) => (
                <span className="flex flex-wrap items-center gap-1.5">
                  {actionLabel(a.action)}
                  {a.admin_in_center && (
                    <span title="Done by an admin inside this center's queue" className="inline-flex items-center gap-1 rounded-full bg-[#E8F0FE] px-2 py-0.5 text-[11px] font-semibold text-[#0A66F0]">
                      <ShieldCheck className="h-3 w-3" /> As Manager
                    </span>
                  )}
                </span>
              ),
            },
            { header: "Center", accessor: (a) => <span className="text-sm">{a.service_center_name || <span className="text-gray-400">—</span>}</span> },
            {
              header: "Record",
              accessor: (a) => <span className="font-mono-num text-xs text-gray-500">{a.target_label || (a.target_id ? `…${a.target_id.slice(-6)}` : "—")}</span>,
            },
          ]}
        />
      </div>

      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}

      <Modal open={!!open} onClose={() => setOpen(null)} title={open ? actionLabel(open.action) : "Activity"}>
        {open && (
          <div className="space-y-3 text-sm">
            <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-2">
              <dt className="text-gray-500">When</dt>
              <dd>{formatDateTime(open.created_at)}</dd>
              <dt className="text-gray-500">Who</dt>
              <dd>
                {open.actor_name || "—"} <Badge tone="neutral" className="ml-1 capitalize">{open.actor_role}</Badge>
              </dd>
              <dt className="text-gray-500">Center</dt>
              <dd>{open.service_center_name || "—"}</dd>
              <dt className="text-gray-500">Area</dt>
              <dd className="capitalize">{open.module.replace(/_/g, " ")}</dd>
              <dt className="text-gray-500">Record</dt>
              <dd className="font-mono-num break-all text-xs">{open.target_label ? `${open.target_label} · ` : ""}{open.target_id || "—"}</dd>
            </dl>
            {open.details && Object.keys(open.details).length > 0 && (
              <div>
                <p className="mb-1.5 text-xs font-medium text-gray-500">Details</p>
                <pre className="max-h-64 overflow-auto rounded-xl bg-gray-50 p-3 text-xs leading-relaxed text-gray-700">{JSON.stringify(open.details, null, 2)}</pre>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
