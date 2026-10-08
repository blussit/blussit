/**
 * The job's money at a glance (spec 1.5): Total · Paid · Wallet Credit Used ·
 * Due for the whole visit, the lines added on site ("Interior Vacuum ₹99 —
 * By You"), and the "Customer Changed This Booking" note (spec 1.3).
 * Captain earnings are never shown here — an add-on doesn't change them.
 */
import { CheckCircle2, PencilLine, PlusCircle } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import type { OnsiteBooking } from "../../api/captainOnsite";
import type { Booking } from "../../types";
import { titleCase } from "../public/landing/shared";
import { carTitle, rupees, visitMoney, type CarTypes } from "./jobState";
import { Notice, Panel } from "./ui";

export function JobMoney({ cars, types }: { cars: Booking[]; types?: CarTypes }) {
  const { t } = useCaptainTranslation();
  const { user } = useAuth();
  const money = visitMoney(cars);
  const isVisit = cars.length > 1;
  const added = cars.flatMap((c, i) =>
    ((c as OnsiteBooking).added_services ?? []).map((line, j) => ({ line, car: c, carNo: i + 1, key: `${c.id}-${j}` })),
  );

  return (
    <Panel className="p-4" >
      <div data-testid="captain-job-money" className="space-y-1.5">
        <Row label={t("captain.money.total")} value={rupees(money.total)} />
        {money.paid > 0 && <Row label={t("captain.money.paid")} value={rupees(money.paid)} tone="green" />}
        {money.wallet > 0 && <Row label={t("captain.money.wallet")} value={rupees(money.wallet)} tone="green" />}
        <div className="mt-2 flex items-center justify-between border-t border-[#E4E9F1] pt-2.5">
          {money.due > 0.004 ? (
            <>
              <span className="text-[15px] font-bold text-[#0E1A33]">{t("captain.money.due")}</span>
              <span className="tabular-nums text-[20px] font-extrabold text-[#0E1A33]">{rupees(money.due)}</span>
            </>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-[15px] font-bold text-[#15803D]">
              <CheckCircle2 className="h-4 w-4" /> {t("captain.money.fullyPaid")}
            </span>
          )}
        </div>
      </div>
      {added.length > 0 && (
        <div className="mt-3 space-y-1.5 rounded-[14px] bg-[#EEF3FA] px-3 py-2.5" data-testid="captain-added-lines">
          {added.map(({ line, car, carNo, key }) => {
            const who = line.by && user?.id && line.by === user.id ? t("captain.onsite.byYou") : t("captain.onsite.by").replace("{name}", line.by_name || titleCase(line.role || "Staff"));
            return (
              <p key={key} className="flex items-start gap-2 text-sm text-[#0E1A33]">
                <PlusCircle className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" />
                <span className="min-w-0 flex-1">
                  <span className="font-semibold text-[#5F6878]">{t("captain.onsite.addedOnSite")}: </span>
                  {isVisit && <span className="font-semibold">{carNo}. {carTitle(car, types)} · </span>}
                  <span className="font-bold">{titleCase(line.name)}{line.qty > 1 ? ` ×${line.qty}` : ""} {rupees(line.amount)}</span>
                  <span className="text-[#5F6878]"> — {who}</span>
                </span>
              </p>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: "green" }) {
  return (
    <div className="flex items-center justify-between gap-3 text-[15px]">
      <span className="font-medium text-[#5F6878]">{label}</span>
      <span className={`tabular-nums font-bold ${tone === "green" ? "text-[#15803D]" : "text-[#0E1A33]"}`}>{value}</span>
    </div>
  );
}

const EDIT_FIELDS = ["services", "vehicle", "address", "date", "slot", "notes", "total"] as const;

/** "Customer Changed This Booking — Services, Time" when the customer edited it. */
export function CustomerEditedNote({ cars }: { cars: Booking[] }) {
  const { t } = useCaptainTranslation();
  const edited = cars.map((c) => c as OnsiteBooking).filter((c) => c.customer_edited_at && c.status !== "cancelled");
  if (!edited.length) return null;
  const fields = new Set(edited.flatMap((c) => c.customer_edited_fields ?? []));
  // The new total shows in the money card; name what the captain acts on.
  const names = EDIT_FIELDS.filter((f) => fields.has(f) && (f !== "total" || fields.size === 1)).map((f) => t(`captain.edited.${f}`));
  return (
    <div data-testid="captain-customer-edited">
      <Notice tone="amber" icon={<PencilLine className="h-4 w-4" />}>
        {t("captain.edited.note").replace(" — {fields}", names.length ? " — {fields}" : "").replace("{fields}", names.join(", "))}
      </Notice>
    </div>
  );
}
