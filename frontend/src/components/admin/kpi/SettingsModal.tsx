/**
 * Admin-entered business inputs behind the Financial and Marketing tabs:
 * cost structure, KPI targets, and the marketing spend log. Everything
 * derived from these is only as honest as what's entered here.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { Button, Input, Modal, Select, Spinner } from "../../ui";
import { kpiApi, type BusinessSettings, type MarketingEntry } from "../../../api/admin";

const SOURCES = ["instagram", "facebook", "google", "organic", "referral", "offline", "society", "other"];

export function BusinessSettingsModal({ open, onClose, initialTab = "costs" }: { open: boolean; onClose: () => void; initialTab?: "costs" | "marketing" }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["business-settings"], queryFn: kpiApi.getSettings, enabled: open });
  const [tab, setTab] = useState<"costs" | "marketing">(initialTab);
  const [form, setForm] = useState<BusinessSettings | null>(null);
  const [entry, setEntry] = useState<MarketingEntry>({ date: new Date().toISOString().slice(0, 10), source: "instagram", campaign: "", spend: 0, leads: 0, customers: 0, revenue: 0 });

  useEffect(() => {
    if (open) setTab(initialTab);
  }, [open, initialTab]);
  useEffect(() => {
    if (data) setForm(structuredClone(data));
  }, [data]);

  const save = useMutation({
    mutationFn: (payload: Partial<BusinessSettings>) => kpiApi.updateSettings(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["business-settings"] });
      qc.invalidateQueries({ queryKey: ["kpi"] });
      onClose();
    },
  });

  const setNum = (key: keyof BusinessSettings) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => (f ? { ...f, [key]: Number(e.target.value) || 0 } : f));
  const setTarget = (key: keyof BusinessSettings["targets"]) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => (f ? { ...f, targets: { ...f.targets, [key]: Number(e.target.value) || 0 } } : f));

  return (
    <Modal open={open} onClose={onClose} title="Business inputs" maxWidth="max-w-2xl">
      {isLoading || !form ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : (
        <div className="space-y-4">
          <div className="flex gap-1 rounded-xl bg-[var(--color-surface)] p-1">
            {(["costs", "marketing"] as const).map((t) => (
              <button key={t} type="button" onClick={() => setTab(t)} className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-semibold capitalize transition-colors ${tab === t ? "bg-white text-[var(--color-text-primary)] shadow-sm" : "text-[var(--color-text-secondary)]"}`}>
                {t === "costs" ? "Costs & targets" : "Marketing spend log"}
              </button>
            ))}
          </div>

          {tab === "costs" && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <Input label="Variable cost per wash (₹)" type="number" value={form.variable_cost_per_wash} onChange={setNum("variable_cost_per_wash")} hint="Water, chemicals, captain payout, travel" />
                <Input label="Fixed cost per month (₹)" type="number" value={form.fixed_cost_monthly} onChange={setNum("fixed_cost_monthly")} hint="Salaries, storage, subscriptions" />
                <Input label="Kit cost (₹)" type="number" value={form.kit_cost} onChange={setNum("kit_cost")} />
                <Input label="Number of kits" type="number" value={form.kits_count} onChange={setNum("kits_count")} />
              </div>
              <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Targets</p>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <Input label="Washes / captain / day" type="number" step="0.1" value={form.targets.washes_per_captain_per_day} onChange={setTarget("washes_per_captain_per_day")} />
                <Input label="Repeat rate %" type="number" value={form.targets.repeat_rate_pct} onChange={setTarget("repeat_rate_pct")} />
                <Input label="Capacity utilisation %" type="number" value={form.targets.capacity_utilisation_pct} onChange={setTarget("capacity_utilisation_pct")} />
                <Input label="Avg rating" type="number" step="0.1" value={form.targets.avg_rating} onChange={setTarget("avg_rating")} />
                <Input label="CAC (₹)" type="number" value={form.targets.cac} onChange={setTarget("cac")} />
              </div>
            </div>
          )}

          {tab === "marketing" && (
            <div className="space-y-4">
              <div className="max-h-44 space-y-1.5 overflow-y-auto">
                {form.marketing_entries.length === 0 && <p className="py-3 text-center text-sm text-[var(--color-text-secondary)]">No spend logged yet.</p>}
                {form.marketing_entries.map((m, i) => (
                  <div key={`${m.date}-${i}`} className="flex items-center gap-2 rounded-lg bg-[var(--color-surface)] px-3 py-2 text-xs">
                    <span className="font-mono-num">{m.date}</span>
                    <span className="capitalize">{m.source}</span>
                    <span className="min-w-0 flex-1 truncate text-[var(--color-text-secondary)]">{m.campaign}</span>
                    <span className="font-mono-num font-semibold">₹{m.spend}</span>
                    <span className="font-mono-num text-[var(--color-text-secondary)]">{m.leads || 0}L · {m.customers || 0}C</span>
                    <button type="button" onClick={() => setForm((f) => (f ? { ...f, marketing_entries: f.marketing_entries.filter((_, j) => j !== i) } : f))} className="text-[var(--color-error)] opacity-60 hover:opacity-100">
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
              <div className="rounded-xl border border-gray-100 p-3">
                <p className="mb-2 text-xs font-semibold text-[var(--color-text-primary)]">Add entry</p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Input label="Date" type="date" value={entry.date} onChange={(e) => setEntry({ ...entry, date: e.target.value })} />
                  <Select label="Source" value={entry.source} onChange={(e) => setEntry({ ...entry, source: e.target.value })}>
                    {SOURCES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </Select>
                  <Input label="Campaign" value={entry.campaign} onChange={(e) => setEntry({ ...entry, campaign: e.target.value })} />
                  <Input label="Spend ₹" type="number" value={entry.spend} onChange={(e) => setEntry({ ...entry, spend: Number(e.target.value) || 0 })} />
                  <Input label="Leads" type="number" value={entry.leads} onChange={(e) => setEntry({ ...entry, leads: Number(e.target.value) || 0 })} />
                  <Input label="Customers" type="number" value={entry.customers} onChange={(e) => setEntry({ ...entry, customers: Number(e.target.value) || 0 })} hint="Attributed to this campaign" />
                  <Input label="Revenue ₹" type="number" value={entry.revenue} onChange={(e) => setEntry({ ...entry, revenue: Number(e.target.value) || 0 })} />
                  <div className="flex items-end">
                    <Button type="button" variant="secondary" className="w-full" onClick={() => {
                      if (!entry.date || !entry.spend) return;
                      setForm((f) => (f ? { ...f, marketing_entries: [...f.marketing_entries, { ...entry }] } : f));
                    }}>
                      Add
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 border-t border-gray-100 pt-4">
            <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
            <Button type="button" isLoading={save.isPending} onClick={() => form && save.mutate(form)}>Save</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
