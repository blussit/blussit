import { SalaryPage } from "../../components/payroll/SalaryPage";
import { useFranchisePaysCaptains } from "../../components/payroll/useFranchisePaysCaptains";
import { Banner } from "../../components/payroll/payrollUi";

/** Manager · Salary — the center's captains: earned, paid, dues, terms, attendance. */
export default function ManagerSalaryPage() {
  const franchisePays = useFranchisePaysCaptains();
  if (franchisePays) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Salary</h1>
        <Banner>Your center's captains are paid by the franchise partner, so there is no salary to manage here.</Banner>
      </div>
    );
  }
  return <SalaryPage scope="manager" />;
}
