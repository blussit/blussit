import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, FileUp } from "lucide-react";
import { couponBundleApi, type BundlePreview, type ImportSummary } from "../../api/couponBundles";
import { BundleSettingsFields, emptyBundleForm, formToSettings, type BundleForm } from "../../components/admin/BundleSettingsFields";
import { CouponTabs } from "../../components/admin/CouponTabs";
import { SearchBox } from "../../components/shared/ListControls";
import { Badge, Button, Card } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";

const PAGE = 50;

// The format rule, shown to the admin exactly as written.
const FORMAT_TEXT =
  "One column only. Put a header (for example Code) in the first row, then one coupon code on every row below it. " +
  "Codes use letters, numbers, dash and underscore, 4 to 32 characters. Up to 2 MB or 100,000 codes per file.";

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

type Step = "upload" | "review" | "details" | "done";

export default function AdminCouponBundleImportPage() {
  const { id: existingId } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<Step>("upload");
  const [error, setError] = useState("");
  const [fileName, setFileName] = useState("");
  const [preview, setPreview] = useState<BundlePreview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [form, setForm] = useState<BundleForm>(emptyBundleForm);
  const [result, setResult] = useState<ImportSummary | null>(null);

  const existing = useQuery({
    queryKey: ["admin-coupon-bundle", existingId],
    queryFn: () => couponBundleApi.get(existingId as string),
    enabled: !!existingId,
  });

  const sample = useMutation({
    mutationFn: () => couponBundleApi.sample(),
    onSuccess: (blob) => saveBlob(blob, "coupon-bundle-sample.csv"),
    onError: (err) => setError(getErrorMessage(err)),
  });

  const parse = useMutation({
    mutationFn: (file: File) => couponBundleApi.preview(file),
    onSuccess: (data) => {
      setPreview(data);
      // Every usable row starts ticked; problem rows are off and locked.
      setSelected(new Set(data.rows.filter((r) => !r[2]).map((r) => r[1])));
      setSearch("");
      setPage(1);
      setProblemsOnly(false);
      setStep("review");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const submit = useMutation({
    mutationFn: async () => {
      const codes = Array.from(selected);
      if (existingId) return couponBundleApi.addCodes(existingId, codes);
      const { settings, error: problem } = formToSettings(form);
      if (!settings) throw new Error(problem);
      return couponBundleApi.create({ ...settings, codes });
    },
    onSuccess: (data) => {
      setResult(data);
      setStep("done");
      queryClient.invalidateQueries({ queryKey: ["admin-coupon-bundles"] });
      queryClient.invalidateQueries({ queryKey: ["admin-coupon-bundle", data.bundle.id] });
      queryClient.invalidateQueries({ queryKey: ["admin-coupon-bundle-codes", data.bundle.id] });
    },
    onError: (err) => setError(err instanceof Error && !("response" in err) ? err.message : getErrorMessage(err)),
  });

  const rows = preview?.rows ?? [];
  const visible = useMemo(() => {
    const needle = search.trim().toUpperCase();
    return rows.filter((r) => (!needle || r[1].includes(needle)) && (!problemsOnly || !!r[2]));
  }, [rows, search, problemsOnly]);
  const pages = Math.max(1, Math.ceil(visible.length / PAGE));
  const shown = visible.slice((page - 1) * PAGE, page * PAGE);

  const setMany = (on: boolean) => {
    const next = new Set(selected);
    for (const r of visible) {
      if (r[2]) continue;
      if (on) next.add(r[1]);
      else next.delete(r[1]);
    }
    setSelected(next);
  };
  const toggle = (code: string) => {
    const next = new Set(selected);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    setSelected(next);
  };

  const onFile = (file: File | undefined) => {
    setError("");
    if (!file) return;
    setFileName(file.name);
    parse.mutate(file);
  };

  const title = existingId ? "Add Codes To Bundle" : "Import Coupon Bundle";
  const goDetails = () => {
    setError("");
    if (!selected.size) return setError("Select at least one code to add.");
    if (existingId) submit.mutate();
    else setStep("details");
  };
  const addNow = () => {
    setError("");
    const { error: problem } = formToSettings(form);
    if (problem) return setError(problem);
    submit.mutate();
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">{title}</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            {existingId ? `Adding to ${existing.data?.name ?? "this bundle"}. The bundle's offer, expiry and uses apply to every new code.` : "Upload a list of codes, choose the ones to keep, then set the offer."}
          </p>
        </div>
        <Link to={existingId ? `/admin/coupons/bundles/${existingId}` : "/admin/coupons/bundles"} className="inline-flex items-center gap-1 text-sm font-medium text-[var(--color-text-secondary)] hover:text-black">
          <ArrowLeft className="h-4 w-4" /> Back
        </Link>
      </div>
      <CouponTabs />

      {step === "upload" && (
        <Card className="space-y-5 p-6">
          <div>
            <h2 className="text-base font-semibold">1. Choose Your CSV File</h2>
            <p className="mt-2 rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3 text-sm text-[var(--color-text-primary)]">{FORMAT_TEXT}</p>
          </div>
          <pre className="rounded-xl bg-gray-50 p-3 font-mono-num text-xs text-gray-700">{"Code\nSUMMER-0001\nSUMMER-0002\nSUMMER-0003"}</pre>
          <div className="flex flex-wrap gap-3">
            <Button variant="outline" onClick={() => sample.mutate()} isLoading={sample.isPending}>
              <Download className="h-4 w-4" /> Download Sample CSV
            </Button>
            <Button onClick={() => fileRef.current?.click()} isLoading={parse.isPending}>
              <FileUp className="h-4 w-4" /> Choose CSV File
            </Button>
            <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        </Card>
      )}

      {step === "review" && preview && (
        <Card className="space-y-4 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">2. Review The Codes</h2>
              <p className="mt-1 text-sm text-gray-500">
                {fileName} · {preview.total.toLocaleString("en-IN")} Rows · {preview.problems.toLocaleString("en-IN")} With Problems (Left Out)
              </p>
            </div>
            <p className="font-mono-num text-sm font-semibold">{selected.size.toLocaleString("en-IN")} Of {preview.ok.toLocaleString("en-IN")} Selected</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search Codes" className="min-w-[200px] flex-1" />
            <Button size="sm" variant="outline" onClick={() => setMany(true)}>Select All</Button>
            <Button size="sm" variant="outline" onClick={() => setMany(false)}>Select None</Button>
            <label className="flex items-center gap-2 text-sm text-gray-600">
              <input type="checkbox" checked={problemsOnly} onChange={(e) => { setProblemsOnly(e.target.checked); setPage(1); }} className="h-4 w-4 accent-black" />
              Problems Only
            </label>
          </div>
          <div className="overflow-x-auto rounded-2xl border border-[var(--color-card-border)]">
            <table className="w-full min-w-[420px] text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--color-card-border)] text-xs font-medium text-gray-500">
                  <th className="w-12 px-4 py-2" />
                  <th className="px-4 py-2">Row</th>
                  <th className="px-4 py-2">Code</th>
                  <th className="px-4 py-2">Problem</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(([row, code, problem]) => (
                  <tr key={`${row}-${code}`} className={problem ? "bg-gray-50 text-gray-400" : "border-t border-gray-50"}>
                    <td className="px-4 py-2">
                      <input type="checkbox" aria-label={`Select ${code || `Row ${row}`}`} className="h-4 w-4 accent-black" checked={!problem && selected.has(code)} disabled={!!problem} onChange={() => toggle(code)} />
                    </td>
                    <td className="font-mono-num px-4 py-2">{row}</td>
                    <td className="font-mono-num px-4 py-2 font-semibold">{code || "—"}</td>
                    <td className="px-4 py-2">{problem ? <Badge tone="warning">{problem}</Badge> : ""}</td>
                  </tr>
                ))}
                {!shown.length && (
                  <tr><td colSpan={4} className="px-4 py-6 text-center text-gray-500">No Codes Match</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <div className="flex items-center justify-center gap-3 text-sm text-gray-500">
              <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <span className="font-mono-num">Page {page} Of {pages}</span>
              <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          )}
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex flex-wrap justify-between gap-3">
            <Button variant="outline" onClick={() => { setStep("upload"); setError(""); }}>Choose Another File</Button>
            {existingId ? (
              <Button onClick={goDetails} isLoading={submit.isPending}>Add Selected To Database</Button>
            ) : (
              <Button onClick={goDetails}>Continue</Button>
            )}
          </div>
        </Card>
      )}

      {step === "details" && (
        <Card className="space-y-5 p-6">
          <h2 className="text-base font-semibold">3. Bundle Details</h2>
          <BundleSettingsFields form={form} setForm={setForm} />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex flex-wrap justify-between gap-3">
            <Button variant="outline" onClick={() => { setStep("review"); setError(""); }}>Back To Codes</Button>
            <Button onClick={addNow} isLoading={submit.isPending}>
              Add Selected To Database ({selected.size.toLocaleString("en-IN")})
            </Button>
          </div>
        </Card>
      )}

      {step === "done" && result && (
        <Card className="space-y-5 p-6">
          <h2 className="text-base font-semibold">Import Complete</h2>
          <div className="grid grid-cols-3 gap-3">
            {[
              ["Added", result.added],
              ["Skipped Duplicates", result.skipped_duplicates],
              ["Invalid", result.invalid],
            ].map(([label, n]) => (
              <div key={label as string} className="rounded-2xl border border-[var(--color-card-border)] p-4">
                <p className="text-xs font-medium text-gray-500">{label}</p>
                <p className="font-mono-num mt-1 text-2xl font-semibold">{(n as number).toLocaleString("en-IN")}</p>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-3">
            <Button onClick={() => navigate(`/admin/coupons/bundles/${result.bundle.id}`)}>Open {result.bundle.name}</Button>
            <Button variant="outline" onClick={() => navigate("/admin/coupons/bundles")}>All Bundles</Button>
          </div>
        </Card>
      )}
    </div>
  );
}
