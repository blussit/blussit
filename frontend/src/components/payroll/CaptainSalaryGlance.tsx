import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ErrorState } from "../ui";
import { payrollApi } from "../../api/payroll";
import { rupeesText } from "../../lib/paise";
import { Banner, BasisBadge, Dues, Fact, FRANCHISE_BANNER, SkeletonRows, termsLine } from "./payrollUi";
import { kitSummary } from "./PayFields";

/**
 * The "Salary" tab of a captain's profile: terms and balances at a glance,
 * with a link into the full Salary page (attendance, commissions, payments).
 */
export function CaptainSalaryGlance({ captainId, base }: { captainId: string; base: "/manager/salary" | "/admin/captain-salary" }) {
  const query = useQuery({ queryKey: ["payroll-captain", captainId], queryFn: () => payrollApi.captain(captainId) });
  if (query.isLoading) return <SkeletonRows rows={3} />;
  if (query.isError || !query.data) return <ErrorState message="Couldn't load this captain's pay." busy={query.isFetching} onRetry={() => void query.refetch()} />;
  const d = query.data;
  const link = (tab: string, label: string) => (
    <Link to={`${base}?captain=${captainId}&tab=${tab}`} className="font-semibold text-[#0A66F0] hover:underline">
      {label}
    </Link>
  );
  return (
    <div className="space-y-4">
      {d.pay.basis === "legacy" && <Banner tone="warning">Still on the old per-job fee. {link("terms", "Set Pay Terms")}</Banner>}
      {!d.finance_impact && <Banner>{FRANCHISE_BANNER}</Banner>}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Fact label="Pay Basis">
          <BasisBadge basis={d.pay.basis} />
        </Fact>
        <Fact label="Terms">
          <span className="text-sm">{termsLine(d.pay)}</span>
        </Fact>
        <Fact label="Dues">
          <Dues paise={d.dues_paise} className="text-base" />
        </Fact>
        <Fact label="Deposit Held">
          <span className="font-mono-num">{rupeesText(d.deposit_held_paise)}</span>
        </Fact>
      </div>
      <p className="text-sm text-[var(--color-text-secondary)]">Kit: {kitSummary(d.pay.kit)}</p>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {link("summary", "Open Full Pay Details")}
        {link("attendance", "Attendance")}
        {link("commissions", "Commissions")}
        {link("payments", "Payments")}
        {link("terms", "Terms, Deposit & Kit")}
      </p>
    </div>
  );
}
