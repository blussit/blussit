import { useQuery } from "@tanstack/react-query";
import { adminServiceCenterApi } from "../../api/admin";
import { useAuth } from "../../context/AuthContext";
import { todayIST } from "../../lib/date";

/**
 * True for a manager whose center's captains are paid by the franchise
 * partner — Blussit pays nobody there, so the Salary page has nothing to show.
 * Same rule as the server's `captain_pay_finance_impact` (franchise type,
 * partner pays captains, in force today). Always false for other roles.
 */
export function useFranchisePaysCaptains(): boolean {
  const { user } = useAuth();
  const centerId = user?.role === "manager" ? user.service_center_id : undefined;
  const query = useQuery({ queryKey: ["manager-own-center", centerId], queryFn: () => adminServiceCenterApi.get(centerId!), enabled: !!centerId, staleTime: 5 * 60_000 });
  const op = query.data?.operation;
  return !!op && op.type === "franchise" && op.captain_pay_managed_by === "franchise" && (!op.effective_from || op.effective_from <= todayIST());
}
