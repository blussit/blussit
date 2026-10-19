import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { holidayApi } from "../api/holidays";

/** True only after the first browser render — never during the build-time
 *  prerender, so holiday UI never changes the pre-rendered HTML. */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

/** Holidays whose notice is showing for this center (the all-centers ones too). */
export function useCenterHolidays(centerId: string | null | undefined) {
  const mounted = useMounted();
  return useQuery({
    queryKey: ["holidays", "center", centerId || ""],
    queryFn: () => holidayApi.publicList(centerId),
    enabled: mounted && !!centerId,
    staleTime: 60_000,
    retry: false,
  });
}
