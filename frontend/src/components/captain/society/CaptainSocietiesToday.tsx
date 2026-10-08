/**
 * Captain: today's societies — "I've arrived" (geo-stamped) and an optional
 * checklist of the cars washed. Self-contained (own data + styles) so the
 * captain app can re-home it anywhere. See docs/SOCIETY_PLANS.md.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, CheckCircle2, MapPin } from "lucide-react";
import { bucketUsage, captainSocietyApi, clockIST, premiumUsage, type CaptainSocietyCard } from "../../../api/society";
import { getErrorMessage } from "../../../lib/api-client";
import { LoadError } from "../ui";
import { titleCase } from "../../public/landing/shared";

type Lang = "en" | "hi";
const T = {
  en: {
    title: "Today's Societies", none: "No society assigned to you today.", arrive: "I've Arrived", arrived: "Arrived", locating: "Getting Location…",
    far: "Far from the society pin — your manager will see this.", noLoc: "Saved without location.", cars: "Cars Washed Today", save: "Save",
    saved: "Saved", left: "Left", allDone: "All Done", sub: "Covering Today",
    bucketHint: "Daily washes done / allowed this cycle", premiumHint: "Premium washes left / this cycle",
    loadError: "Couldn't Load Today's Societies", loadErrorSub: "Check your internet and try again.", tryAgain: "Try Again",
  },
  hi: {
    title: "आज की सोसाइटी", none: "आज कोई सोसाइटी नहीं।", arrive: "मैं पहुँच गया", arrived: "पहुँचे", locating: "लोकेशन ले रहे हैं…",
    far: "सोसाइटी से दूर — मैनेजर को दिखेगा।", noLoc: "बिना लोकेशन सेव हुआ।", cars: "आज धुली गाड़ियाँ", save: "सेव करें",
    saved: "सेव हो गया", left: "बाकी", allDone: "पूरा", sub: "आज के लिए",
    bucketHint: "इस साइकिल में हुई / कुल डेली वॉश", premiumHint: "बाकी / कुल प्रीमियम वॉश",
    loadError: "आज की सोसाइटी लोड नहीं हो सकीं", loadErrorSub: "इंटरनेट जाँचें और फिर से कोशिश करें।", tryAgain: "फिर से कोशिश करें",
  },
};

function locate(): Promise<{ latitude?: number; longitude?: number; accuracy_m?: number }> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve({});
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy_m: p.coords.accuracy ?? undefined }),
      () => resolve({}),
      { enableHighAccuracy: true, timeout: 15000 },
    );
  });
}

export function CaptainSocietiesToday({ language = "en", hideWhenEmpty = false }: { language?: Lang; hideWhenEmpty?: boolean }) {
  const t = T[language];
  const q = useQuery({ queryKey: ["captain-societies-today"], queryFn: captainSocietyApi.today, refetchInterval: 120_000 });
  if (q.isLoading) return null;
  const list = q.data?.societies || [];
  // A failed read is not "no society today" — show it even when the empty
  // case would be hidden, so a captain with a society doesn't miss it.
  const failed = q.isError && !q.data;
  if (!list.length && hideWhenEmpty && !failed) return null;
  return (
    <section className="space-y-3" data-testid="captain-societies">
      <h2 className="flex items-center gap-2 text-base font-bold text-[#0E1A33]"><Building2 className="h-5 w-5" /> {t.title}</h2>
      {failed && <LoadError title={t.loadError} sub={t.loadErrorSub} retryLabel={t.tryAgain} onRetry={() => void q.refetch()} busy={q.isFetching} />}
      {!list.length && !q.isError && <p className="rounded-2xl border border-[#E4E9F1] bg-white p-4 text-sm text-[#5F6878]">{t.none}</p>}
      {list.map((s) => <SocietyCard key={s.id} society={s} lang={language} />)}
    </section>
  );
}

function SocietyCard({ society, lang }: { society: CaptainSocietyCard; lang: Lang }) {
  const t = T[lang];
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set(society.cars.filter((c) => c.washed_today).map((c) => c.vehicle_id)));
  const [locating, setLocating] = useState(false);
  const update = (card: CaptainSocietyCard) => {
    queryClient.setQueryData<{ date: string; societies: CaptainSocietyCard[] }>(["captain-societies-today"], (old) =>
      old ? { ...old, societies: old.societies.map((s) => (s.id === card.id ? card : s)) } : old);
  };
  const arrive = useMutation({
    mutationFn: async () => {
      setLocating(true);
      const loc = await locate().finally(() => setLocating(false));
      return captainSocietyApi.arrive(society.id, loc);
    },
    onSuccess: (card) => { setError(""); update(card); },
    onError: (err) => setError(getErrorMessage(err)),
  });
  const save = useMutation({
    mutationFn: () => captainSocietyApi.washed(society.id, [...checked]),
    onSuccess: (card) => { setError(""); setNote(t.saved); update(card); },
    onError: (err) => setError(getErrorMessage(err)),
  });
  const att = society.attendance;
  return (
    <div className="rounded-2xl border border-[#E4E9F1] bg-white p-4" data-testid="captain-society-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-bold text-[#0E1A33]">{society.name}</p>
          <p className="flex items-center gap-1 text-xs text-[#5F6878]"><MapPin className="h-3.5 w-3.5 shrink-0" /> <span className="truncate">{[society.address_line, society.area].filter(Boolean).join(", ")}</span></p>
          {society.is_substitute && <p className="mt-0.5 text-xs font-semibold text-[#0A66F0]">{t.sub}</p>}
        </div>
        {att && <span className="flex shrink-0 items-center gap-1 rounded-full bg-green-50 px-2.5 py-1 text-xs font-bold text-green-700"><CheckCircle2 className="h-3.5 w-3.5" /> {t.arrived} {clockIST(att.arrived_at)}</span>}
      </div>
      {!att ? (
        <button type="button" disabled={arrive.isPending} onClick={() => arrive.mutate()}
          className="mt-3 flex h-12 w-full items-center justify-center rounded-xl bg-[#0A66F0] text-[15px] font-bold text-white disabled:opacity-60">
          {locating ? t.locating : t.arrive}
        </button>
      ) : (
        <>
          {att.far_from_society && <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{t.far}</p>}
          {att.location_missing && <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{t.noLoc}</p>}
          {society.cars.length > 0 && (
            <div className="mt-3">
              <p className="mb-2 text-sm font-semibold text-[#0E1A33]">{t.cars} · {checked.size}/{society.cars.length}</p>
              <ul className="space-y-1.5">
                {society.cars.map((c) => {
                  const on = checked.has(c.vehicle_id);
                  const blocked = !on && c.allowance_left <= 0;
                  return (
                    <li key={c.vehicle_id}>
                      <label className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 ${on ? "border-[#0A66F0] bg-[#E8F0FE]" : "border-[#E4E9F1]"} ${blocked ? "opacity-50" : ""}`}>
                        <input type="checkbox" className="h-5 w-5 accent-[#0A66F0]" checked={on} disabled={blocked}
                          onChange={() => { setNote(""); setChecked((prev) => { const n = new Set(prev); if (n.has(c.vehicle_id)) n.delete(c.vehicle_id); else n.add(c.vehicle_id); return n; }); }} />
                        <span className="min-w-0 flex-1">
                          <span className="block font-mono text-sm font-bold">{c.registration_number}</span>
                          <span className="block text-xs text-[#5F6878]">{[c.flat, titleCase(c.vehicle_type_name)].filter(Boolean).join(" · ")}</span>
                        </span>
                        <span className="text-right text-xs text-[#5F6878]">
                          <span className={`block ${c.allowance_left > 0 ? "" : "font-semibold text-[#0E1A33]"}`} title={t.bucketHint}>
                            {bucketUsage(c.bucket_label, c.used, c.allowance)}{c.allowance_left > 0 ? "" : ` · ${t.allDone}`}
                          </span>
                          <span className="block" title={t.premiumHint}>{premiumUsage(c.premium_service_name, c.premium_remaining, c.premium_total)}</span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
              <button type="button" disabled={save.isPending} onClick={() => save.mutate()}
                className="mt-3 flex h-11 w-full items-center justify-center rounded-xl border border-[#0A66F0] text-sm font-bold text-[#0A66F0] disabled:opacity-60">
                {t.save}
              </button>
              {note && <p className="mt-1.5 text-center text-xs text-green-700">{note}</p>}
            </div>
          )}
        </>
      )}
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </div>
  );
}
