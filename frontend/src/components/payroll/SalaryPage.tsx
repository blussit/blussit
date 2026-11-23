import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Download, Users, UserRound } from "lucide-react";
import { adminServiceCenterApi } from "../../api/admin";
import { payrollApi, type PayrollCaptainRow } from "../../api/payroll";
import { Badge, Button, DataTable, EmptyState, ErrorState, Input, Panel, Select, StatCard, StatusBadge } from "../ui";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { cn } from "../../lib/cn";
import { rupeesText } from "../../lib/paise";
import { CaptainPayDrawer, DRAWER_TABS, type DrawerTab } from "./CaptainPayDrawer";
import { Banner, BasisBadge, Dues, FRANCHISE_BANNER, MonthPicker, SkeletonRows, currentMonth, monthLabel, termsLine } from "./payrollUi";

const TAB_KEYS = DRAWER_TABS.map((t) => t.key as string);

/**
 * The Salary page. The manager sees their own center; the admin sees every
 * center and can filter by one. Rows open the captain's pay dialog.
 */
export function SalaryPage({ scope }: { scope: "manager" | "admin" }) {
  const { push } = useToast();
  const [params, setParams] = useSearchParams();
  const [month, setMonth] = useState(currentMonth());
  const [basis, setBasis] = useState("");
  const [status, setStatus] = useState("active");
  const [search, setSearch] = useState("");
  const [center, setCenter] = useState("");

  // A link from another screen can open one captain straight on a tab.
  const openId = params.get("captain");
  const tabParam = params.get("tab") || "";
  const openTab: DrawerTab = TAB_KEYS.includes(tabParam) ? (tabParam as DrawerTab) : "summary";
  const setOpen = (id: string | null, tab?: DrawerTab) => {
    const next = new URLSearchParams(params);
    if (id) {
      next.set("captain", id);
      if (tab) next.set("tab", tab);
      else next.delete("tab");
    } else {
      next.delete("captain");
      next.delete("tab");
    }
    setParams(next, { replace: true });
  };

  const centersQuery = useQuery({
    queryKey: ["admin-centers-lite"],
    queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }),
    enabled: scope === "admin",
  });
  const filters = { center_id: scope === "admin" ? center : "", month, basis, status };
  const query = useQuery({ queryKey: ["payroll-captains", filters], queryFn: () => payrollApi.captains(filters) });
  const data = query.data;

  const exporting = useMutation({
    mutationFn: () => payrollApi.exportCsv(filters),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `blussit-captain-pay-${month}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    },
    onError: (err) => push({ tone: "error", title: "Couldn't Export", message: getErrorMessage(err) }),
  });

  const needle = search.trim().toLowerCase();
  const rows = useMemo(
    () => (data?.rows || []).filter((r) => !needle || `${r.full_name} ${r.phone || ""} ${r.employee_id || ""}`.toLowerCase().includes(needle)),
    [data, needle],
  );
  const configured = rows.filter((r) => r.pay.basis !== "legacy");
  const legacy = rows.filter((r) => r.pay.basis === "legacy");
  const s = data?.summary;
  const anyFranchise = (data?.rows || []).some((r) => !r.finance_impact);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">{scope === "admin" ? "Captain Salary" : "Salary"}</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            {scope === "admin" ? "What every captain earned, was paid and is still owed — gross amounts, before any tax." : "What your captains earned, were paid and are still owed — gross amounts, before any tax."}
          </p>
        </div>
        <Button variant="outline" isLoading={exporting.isPending} onClick={() => exporting.mutate()}>
          <Download className="h-4 w-4" /> Export CSV
        </Button>
      </div>

      <div className="grid grid-cols-1 items-end gap-3 sm:flex sm:flex-wrap">
        <div>
          <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Month</p>
          <MonthPicker value={month} onChange={setMonth} />
        </div>
        {scope === "admin" && (
          <div className="sm:w-52">
            <Select label="Service Center" value={center} onChange={(e) => setCenter(e.target.value)}>
              <option value="">All Centers</option>
              {(centersQuery.data?.data || []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </div>
        )}
        <div className="sm:w-44">
          <Select label="Pay Basis" value={basis} onChange={(e) => setBasis(e.target.value)}>
            <option value="">All</option>
            <option value="salary">Salary</option>
            <option value="commission">Commission</option>
            <option value="legacy">Legacy</option>
          </Select>
        </div>
        <div className="sm:w-40">
          <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">Active</option>
            <option value="suspended">Suspended</option>
            <option value="">All</option>
          </Select>
        </div>
        <div className="sm:min-w-[220px] sm:flex-1 sm:max-w-sm">
          <Input label="Search" type="search" placeholder="Name, phone or ID" value={search} onChange={(e) => setSearch(e.target.value)} autoComplete="off" />
        </div>
      </div>

      {scope === "admin" && centersQuery.isError && !centersQuery.data && (
        <ErrorState message="Couldn't load service centers." busy={centersQuery.isFetching} onRetry={() => void centersQuery.refetch()} className="p-4" />
      )}

      {query.isLoading ? (
        <SkeletonRows rows={6} />
      ) : query.isError && !data ? (
        <ErrorState message="Couldn't load captain pay." busy={query.isFetching} onRetry={() => void query.refetch()} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Active Captains" value={s?.active_captains ?? 0} icon={Users} hint={s && s.legacy_captains > 0 ? `${s.legacy_captains} still on the old per-job fee` : undefined} />
            <StatCard label="Earned This Month" value={rupeesText(s?.earned_paise)} hint={monthLabel(month)} />
            <StatCard label="Paid This Month" value={rupeesText(s?.paid_paise)} hint={monthLabel(month)} tone="success" />
            <StatCard
              label="Total Dues"
              value={rupeesText(Math.abs(s?.dues_paise || 0))}
              tone={(s?.dues_paise || 0) < 0 ? "error" : (s?.dues_paise || 0) > 0 ? "warning" : "default"}
              hint={(s?.dues_paise || 0) < 0 ? "Recovery due — paid ahead" : "Still to pay, all time"}
            />
          </div>

          {anyFranchise && <Banner>{FRANCHISE_BANNER}</Banner>}
          {data?.center?.kit_warning && <Banner tone="warning">{data.center.kit_warning}</Banner>}

          {!!legacy.length && basis !== "legacy" && (
            <Panel title="Set Pay Terms" description={`${legacy.length} ${legacy.length === 1 ? "captain is" : "captains are"} still on the old per-job fee. Choose salary or commission for each.`}>
              <ul className="divide-y divide-gray-100">
                {legacy.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 first:pt-0 last:pb-0">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-[var(--color-text-primary)]">{r.full_name}</p>
                      <p className="text-xs text-[var(--color-text-secondary)]">{[r.employee_id, r.service_center_name].filter(Boolean).join(" · ") || "Captain"}</p>
                    </div>
                    <Button size="sm" onClick={() => setOpen(r.id, "terms")}>
                      Set Pay Terms
                    </Button>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {!rows.length ? (
            <EmptyState
              icon={UserRound}
              title={needle ? `No Captains Match “${search.trim()}”` : "No Captains Here"}
              description={needle ? undefined : "Captains you add show up here with their pay."}
            />
          ) : (
            (!!configured.length || basis === "legacy") && (
              <>
                <div className="hidden md:block">
                  <DataTable<PayrollCaptainRow>
                    data={basis === "legacy" ? legacy : configured}
                    onRowClick={(r) => setOpen(r.id)}
                    columns={[
                      {
                        header: "Captain",
                        accessor: (r) => (
                          <div className="min-w-0">
                            <p className="font-medium text-[var(--ui-ink,#0E1A33)]">{r.full_name}</p>
                            <p className="text-xs text-[var(--color-text-secondary)]">{[r.employee_id, scope === "admin" ? r.service_center_name : null].filter(Boolean).join(" · ")}</p>
                          </div>
                        ),
                      },
                      { header: "Basis", accessor: (r) => <BasisBadge basis={r.pay.basis} /> },
                      { header: "Terms", accessor: (r) => <span className="whitespace-nowrap">{termsLine(r.pay)}</span> },
                      { header: "Earned", accessor: (r) => <span className="font-mono-num">{rupeesText(r.earned_paise)}</span> },
                      { header: "Paid", accessor: (r) => <span className="font-mono-num">{rupeesText(r.paid_paise)}</span> },
                      { header: "Dues", accessor: (r) => <Dues paise={r.dues_paise} /> },
                      { header: "Deposit Held", accessor: (r) => <span className="font-mono-num">{rupeesText(r.deposit_held_paise)}</span> },
                      { header: "Kit", accessor: (r) => <KitChip row={r} /> },
                      { header: "Pending Commissions", accessor: (r) => (r.pending_commissions > 0 ? <Badge tone="warning">{r.pending_commissions} Not Set</Badge> : <span className="text-gray-300">—</span>) },
                    ]}
                  />
                </div>
                <ul className="space-y-3 md:hidden">
                  {(basis === "legacy" ? legacy : configured).map((r) => (
                    <li key={r.id}>
                      <button
                        type="button"
                        onClick={() => setOpen(r.id)}
                        className="block w-full rounded-2xl border border-[var(--color-card-border)] bg-white p-4 text-left transition-colors hover:border-[#0A66F0]"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate font-semibold text-[var(--ui-ink,#0E1A33)]">{r.full_name}</p>
                            <p className="text-xs text-[var(--color-text-secondary)]">{termsLine(r.pay)}</p>
                          </div>
                          <BasisBadge basis={r.pay.basis} />
                        </div>
                        <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
                          <div>
                            <dt className="text-[var(--color-text-secondary)]">Earned</dt>
                            <dd className="font-mono-num font-semibold">{rupeesText(r.earned_paise)}</dd>
                          </div>
                          <div>
                            <dt className="text-[var(--color-text-secondary)]">Paid</dt>
                            <dd className="font-mono-num font-semibold">{rupeesText(r.paid_paise)}</dd>
                          </div>
                          <div>
                            <dt className="text-[var(--color-text-secondary)]">Dues</dt>
                            <dd>
                              <Dues paise={r.dues_paise} className="text-xs" />
                            </dd>
                          </div>
                        </dl>
                        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
                          {r.status !== "active" && <StatusBadge status={r.status} />}
                          {r.deposit_held_paise > 0 && <Badge tone="neutral">Deposit {rupeesText(r.deposit_held_paise)}</Badge>}
                          <KitChip row={r} />
                          {r.pending_commissions > 0 && <Badge tone="warning">{r.pending_commissions} Commission Not Set</Badge>}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )
          )}
        </>
      )}

      <CaptainPayDrawer captainId={openId} month={month} onMonthChange={setMonth} onClose={() => setOpen(null)} initialTab={openTab} />
    </div>
  );
}

function KitChip({ row }: { row: PayrollCaptainRow }) {
  if (!row.kit_provided) return <span className={cn("text-xs text-gray-400")}>No Kit</span>;
  return <Badge tone={row.kit_returned ? "neutral" : "info"}>{row.kit_returned ? "Kit Returned" : "Kit Issued"}</Badge>;
}
