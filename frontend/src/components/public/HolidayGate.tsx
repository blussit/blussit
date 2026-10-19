import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { holidayApi, type PublicHoliday } from "../../api/holidays";
import { useAuth } from "../../context/AuthContext";
import { holidaySeen, markHolidaySeen, setHolidayGate } from "../../lib/holidayGate";
import { useMounted } from "../../hooks/useCenterHolidays";

// The card itself is a separate chunk — only downloaded when a holiday is
// actually being announced, so the landing bundle barely grows.
const HolidayPopup = lazy(() => import("./HolidayPopup"));

/**
 * Shows the first holiday in `holidays` this browser hasn't seen this
 * session — once (sessionStorage per holiday id). `onDecided(true)` when
 * one opens, `(false)` when there was nothing new to show.
 */
export function HolidayPopupHost({
  holidays,
  bookPath,
  onDecided,
}: {
  holidays: PublicHoliday[] | undefined;
  bookPath: string;
  onDecided?: (shown: boolean) => void;
}) {
  const [current, setCurrent] = useState<PublicHoliday | null>(null);
  const [open, setOpen] = useState(false);
  const decided = useRef(false);
  useEffect(() => {
    if (!holidays || current) return;
    const next = holidays.find((h) => !holidaySeen(h.id));
    if (!next) {
      if (!decided.current) onDecided?.(false);
      decided.current = true;
      return;
    }
    markHolidaySeen(next.id);
    decided.current = true;
    onDecided?.(true);
    setCurrent(next);
    setOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holidays, current]);
  if (!current) return null;
  return (
    <Suspense fallback={null}>
      <HolidayPopup holiday={current} open={open} onClose={() => setOpen(false)} bookPath={bookPath} />
    </Suspense>
  );
}

/**
 * Mounted once in App: on the public website (all-centers holidays) and in
 * the signed-in customer app (their center's, via /holidays/mine). Staff
 * consoles never see it. Tells LaunchOfferPopup whether a holiday took
 * this visit (lib/holidayGate).
 */
export function HolidayGate() {
  const mounted = useMounted();
  const { pathname } = useLocation();
  const { user, isLoading } = useAuth();
  const staffArea = /^\/(admin|manager|captain)(\/|$)/.test(pathname);
  const inApp = pathname === "/app" || pathname.startsWith("/app/");
  const isCustomer = user?.role === "customer";
  const useMine = inApp && isCustomer;
  const enabled = mounted && !isLoading && !staffArea && (!inApp || isCustomer);
  const { data, isError } = useQuery({
    queryKey: useMine ? ["holidays", "mine"] : ["holidays", "public"],
    queryFn: () => (useMine ? holidayApi.mine() : holidayApi.publicList()),
    enabled,
    staleTime: 60_000,
    retry: false,
  });
  useEffect(() => {
    if (staffArea || isError) setHolidayGate("none");
  }, [staffArea, isError]);
  if (!enabled || !data) return null;
  return (
    <HolidayPopupHost
      key={useMine ? "mine" : "public"}
      holidays={data}
      bookPath={isCustomer ? "/app/book" : "/book"}
      onDecided={(shown) => setHolidayGate(shown ? "showing" : "none")}
    />
  );
}
