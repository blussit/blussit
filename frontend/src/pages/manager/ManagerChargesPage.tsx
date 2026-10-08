import { ChargesList } from "../../components/shared/CancellationCharges";

/** The manager's own center's late-cancellation charges — reduce or waive
 *  one, and see every change on it. */
export default function ManagerChargesPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Cancellation Charges</h1>
        <p className="mt-1 max-w-3xl text-sm text-[var(--color-text-secondary)]">
          A late cancellation is charged to the customer&apos;s wallet. Reduce or waive it here — the difference goes back to their wallet, or comes off the
          unpaid booking carrying it.
        </p>
      </div>
      <ChargesList />
    </div>
  );
}
