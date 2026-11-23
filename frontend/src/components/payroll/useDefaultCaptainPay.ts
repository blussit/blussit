import { useQuery } from "@tanstack/react-query";
import { adminServiceCenterApi } from "../../api/admin";
import type { DefaultCaptainPay } from "../../api/payroll";

/**
 * The center's suggested pay for a new captain (service_centers.operation
 * .default_captain_pay) — only a prefill, never forced. Quietly returns
 * nothing while loading, when no center is picked, or when the API has none.
 */
export function useDefaultCaptainPay(centerId: string | null | undefined, enabled = true) {
  const query = useQuery({
    queryKey: ["center-detail-for-pay", centerId],
    queryFn: () => adminServiceCenterApi.get(centerId!),
    enabled: !!centerId && enabled,
    staleTime: 5 * 60 * 1000,
  });
  const def = query.data?.operation?.default_captain_pay as DefaultCaptainPay | null | undefined;
  const usable = def && (def.basis === "salary" || def.basis === "commission") ? def : null;
  return { defaultPay: usable, loaded: query.isSuccess || query.isError, centerId: centerId || "" };
}
