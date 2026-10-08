/**
 * Captain: society premium-wash visit days they're on ("Society visit —
 * Sunrise Residency · 6 premium washes") with the car list — each booked
 * car opens the normal job flow. Read-only; self-contained like
 * CaptainSocietiesToday so the captain app can re-home it.
 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ChevronRight, MapPin, Sparkles } from "lucide-react";
import { scheduleApi, type CaptainVisit } from "../../../api/societySchedule";
import { formatDay, todayIST } from "../../../lib/date";
import { addDays } from "../../society/schedule/scheduleUi";
import { titleCase } from "../../public/landing/shared";
import { LoadError } from "../ui";

type Lang = "en" | "hi";
const T = {
  en: {
    visit: "Society Visit", washes: (n: number) => `${n} Premium Wash${n === 1 ? "" : "es"}`, upcoming: "Coming Up",
    confirmed: (d: string) => `Cars are confirmed on ${d}`, open: "Open Job", planned: "Planned", directions: "Directions",
    loadError: "Couldn't Load Your Society Visits", loadErrorSub: "Check your internet and try again.", tryAgain: "Try Again",
  },
  hi: {
    visit: "सोसाइटी विज़िट", washes: (n: number) => `${n} प्रीमियम वॉश`, upcoming: "आगे",
    confirmed: (d: string) => `गाड़ियाँ ${d} को पक्की होंगी`, open: "जॉब खोलें", planned: "योजना में", directions: "रास्ता",
    loadError: "सोसाइटी विज़िट लोड नहीं हो सकीं", loadErrorSub: "इंटरनेट जाँचें और फिर से कोशिश करें।", tryAgain: "फिर से कोशिश करें",
  },
};

const STATUS: Record<string, string> = {
  pending: "Needs Captain", assigned: "Assigned", captain_on_the_way: "On The Way", service_started: "Washing",
  completed: "Done", cancelled: "Cancelled", projected: "Planned", booked: "Booked",
};

export function CaptainSocietyVisits({ language = "en", hideWhenEmpty = false }: { language?: Lang; hideWhenEmpty?: boolean }) {
  const t = T[language];
  const q = useQuery({ queryKey: ["captain-society-visits"], queryFn: scheduleApi.captainVisits, refetchInterval: 120_000 });
  if (q.isLoading) return null;
  // A failed read is not "no visits" — say so even where empty is hidden.
  if (q.isError && !q.data)
    return <LoadError title={t.loadError} sub={t.loadErrorSub} retryLabel={t.tryAgain} onRetry={() => void q.refetch()} busy={q.isFetching} />;
  const visits = q.data?.visits || [];
  if (!visits.length && hideWhenEmpty) return null;
  const today = todayIST();
  // Today and tomorrow in full (tomorrow's list lets him plan the day);
  // anything later as a short "coming up" list.
  const tomorrow = addDays(today, 1);
  const now = visits.filter((v) => v.date <= tomorrow);
  const later = visits.filter((v) => v.date > tomorrow);
  return (
    <section className="space-y-3" data-testid="captain-society-visits">
      {now.map((v) => <VisitCard key={v.id} visit={v} lang={language} />)}
      {later.length > 0 && (
        <div className="rounded-2xl border border-[#E4E9F1] bg-white p-4">
          <p className="mb-2 text-[13px] font-bold uppercase tracking-wide text-[#5F6878]">{t.visit} · {t.upcoming}</p>
          <ul className="space-y-2">
            {later.slice(0, 3).map((v) => (
              <li key={v.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="min-w-0">
                  <span className="block font-bold text-[#0E1A33]">{formatDay(v.date)} · {v.society_name}</span>
                  <span className="block text-xs text-[#5F6878]">{v.window_label} · {t.washes(v.my_washes || v.total_washes)}</span>
                </span>
                <CalendarClock className="h-4 w-4 shrink-0 text-[#0A66F0]" />
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function VisitCard({ visit, lang }: { visit: CaptainVisit; lang: Lang }) {
  const t = T[lang];
  const maps = visit.latitude != null && visit.longitude != null ? `https://www.google.com/maps/dir/?api=1&destination=${visit.latitude},${visit.longitude}` : null;
  return (
    <div className="overflow-hidden rounded-2xl border border-[#E4E9F1] bg-white" data-testid="captain-society-visit">
      <div className="px-4 pt-4">
        <p className="flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-wide text-[#5F6878]"><Sparkles className="h-4 w-4 text-[#0A66F0]" /> {t.visit} · {formatDay(visit.date)}</p>
        <p className="mt-1 text-[18px] font-extrabold leading-snug text-[#0E1A33]">{visit.society_name} · {t.washes(visit.my_washes || visit.total_washes)}</p>
        <p className="mt-0.5 text-sm text-[#5F6878]">{visit.window_label}</p>
        {visit.address && <p className="mt-1 flex items-center gap-1 text-xs text-[#5F6878]"><MapPin className="h-3.5 w-3.5 shrink-0" /> <span className="truncate">{visit.address}</span></p>}
        {visit.status === "planned" && visit.generate_on && <p className="mt-1 text-xs font-semibold text-[#9A6400]">{t.confirmed(formatDay(visit.generate_on))}</p>}
      </div>
      <ul className="mt-3 divide-y divide-[#EEF2F7] border-t border-[#E4E9F1]">
        {visit.cars.map((c, i) => {
          const body = (
            <>
              <span className="w-[70px] shrink-0 text-sm font-bold text-[#0E1A33]">{c.start_label || "—"}</span>
              <span className="min-w-0 flex-1">
                <span className="block font-mono text-sm font-bold text-[#0E1A33]">{c.plate || "—"}</span>
                <span className="block text-xs text-[#5F6878]">{[c.flat, titleCase(c.vehicle_type_name), titleCase(c.service_name)].filter(Boolean).join(" · ")}</span>
              </span>
              <span className="text-xs font-semibold text-[#5F6878]">{STATUS[c.status] || titleCase(c.status.replace(/_/g, " "))}</span>
              {c.booking_id && <ChevronRight className="h-4 w-4 text-[#A3AAB6]" />}
            </>
          );
          return (
            <li key={`${c.plate}-${i}`}>
              {c.booking_id ? (
                <Link to={`/captain/jobs/${c.booking_id}`} className="flex min-h-[56px] items-center gap-3 px-4 active:bg-[#EEF3FA]" aria-label={`${t.open} ${c.plate}`}>{body}</Link>
              ) : (
                <div className="flex min-h-[56px] items-center gap-3 px-4">{body}</div>
              )}
            </li>
          );
        })}
      </ul>
      {maps && (
        <div className="border-t border-[#E4E9F1] p-3">
          <a href={maps} target="_blank" rel="noreferrer" className="flex min-h-[48px] items-center justify-center gap-2 rounded-2xl bg-[#E8F0FE] text-[15px] font-bold text-[#0A66F0]">
            <MapPin className="h-5 w-5" /> {t.directions}
          </a>
        </div>
      )}
    </div>
  );
}
