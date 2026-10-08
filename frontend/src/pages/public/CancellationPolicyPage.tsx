import { useQuery } from "@tanstack/react-query";
import { Clock, IndianRupee } from "lucide-react";
import { bookingPolicyApi } from "../../api/catalog";
import { PolicyCard, PolicyPage, Points } from "./PolicyKit";

/**
 * One rule, one table: free until 4 hours before the slot, then a fixed
 * charge by how late it is (founder rule 2026-10-07). Money moves through
 * the customer's Blussit wallet: a paid booking's money minus the charge
 * is credited there; an unpaid booking's charge is added to the next one.
 * The amounts are the live booking policy (admin Settings & Pricing); the
 * build pre-renders them when the API answers, and 50/80/100 are only the
 * fallback.
 */
const FALLBACK = { oneToFour: 50, underOne: 80, captainLeft: 100 };

/** Whole-number policy value, else the fallback. */
const whole = (v: unknown, fallback: number) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.round(v) : fallback);
/** 60 → "1 hour", 90 → "90 minutes". */
const minutesText = (m: number) => (m % 60 === 0 ? `${m / 60} hour${m === 60 ? "" : "s"}` : `${m} minutes`);

const rupees = (v: unknown, fallback: number) => {
  const n = typeof v === "number" && Number.isFinite(v) ? Math.round(v) : fallback;
  return n > 0 ? `₹${n}` : "Free";
};

export default function CancellationPolicyPage() {
  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get, staleTime: 5 * 60 * 1000 });
  const live = policy as (typeof policy & { free_cancel_hours?: number; customer_edit_lock_minutes?: number; plan_wash_forfeit_minutes?: number }) | undefined;
  const freeHours = whole(live?.free_cancel_hours, 4);
  const editLock = minutesText(whole(live?.customer_edit_lock_minutes, 60));
  const planForfeit = minutesText(whole(live?.plan_wash_forfeit_minutes, 60));
  const DEDUCTIONS = [
    { when: `More than ${freeHours} hours before your slot`, amount: "Free", free: true },
    { when: `1 to ${freeHours} hours before your slot`, amount: rupees(policy?.cancellation_fee_1_to_4h, FALLBACK.oneToFour) },
    { when: "Less than 1 hour before your slot", amount: rupees(policy?.cancellation_fee_under_1h, FALLBACK.underOne) },
    { when: "After the captain has left for your address", amount: rupees(policy?.cancellation_fee_after_captain_left, FALLBACK.captainLeft) },
  ];
  return (
    <PolicyPage
      path="/cancellation-policy"
      title="Cancellation Policy"
      intro={`Cancel free up to ${freeHours} hours before your slot. After that, a small charge applies — the later you cancel, the more it is.`}
      updated="7 October 2026"
    >
      <section className="overflow-hidden rounded-[18px] border border-[#E4EBF5] bg-white">
        <div className="flex items-center gap-3 px-5 pt-5 md:px-6 md:pt-6">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#EEF4FF]">
            <Clock className="h-[18px] w-[18px] text-[#0A66F0]" aria-hidden="true" />
          </span>
          <h2 className="font-display text-[18px] font-bold md:text-[19px]">When You Cancel And What It Costs</h2>
        </div>
        <table className="mt-4 w-full text-left text-[15px]">
          <thead>
            <tr className="border-y border-[#E4EBF5] bg-[#F7F9FC] text-[13px] font-bold uppercase tracking-[0.06em] text-[#5F6878]">
              <th scope="col" className="px-5 py-3 md:px-6">When You Cancel</th>
              <th scope="col" className="px-5 py-3 text-right md:px-6">Charge</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#E4EBF5]">
            {DEDUCTIONS.map((row) => (
              <tr key={row.when}>
                <td className="px-5 py-4 font-medium md:px-6">{row.when}</td>
                <td className={`whitespace-nowrap px-5 py-4 text-right font-display text-[17px] font-extrabold md:px-6 ${row.free ? "text-[#0F8A4B]" : ""}`}>
                  {row.amount}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <PolicyCard icon={IndianRupee} title="How It Works">
        <Points
          items={[
            "Cancel it yourself in the app until the captain heads to you. The charge goes by time, as in the table.",
            "Paid online? The amount minus any charge goes to your Blussit wallet. Use it on your next booking, or ask your manager to send it to your bank/UPI.",
            "Didn't pay? The charge is added to your next booking.",
            `Plan washes: no money charge. Cancel less than ${planForfeit} before the slot and that wash is used up.`,
            `Changes (date, address, services) are allowed until ${editLock} before your slot.`,
          ]}
        />
      </PolicyCard>
    </PolicyPage>
  );
}
