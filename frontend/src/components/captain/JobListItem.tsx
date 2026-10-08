import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { formatDay, formatTime12 } from "../../lib/date";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import type { BookingSlab } from "../../lib/bookingGroups";
import { blockedByFlag, clock, currentCar, needsManager, paymentOf, rupees, stepOf, useCarTypes, visitCarService } from "./jobState";
import { Pill } from "./ui";

/** One row in the Jobs tab — a multi-car visit is ONE row. */
export function JobListItem({ slab }: { slab: BookingSlab }) {
  const { t } = useCaptainTranslation();
  const car = currentCar(slab);
  const step = stepOf(car);
  const pay = paymentOf(slab.bookings);
  const day = formatDay(car.scheduled_date);
  const done = step === "done";
  const types = useCarTypes(slab.bookings);

  const status = needsManager(car) || blockedByFlag(car)
    ? { tone: "amber" as const, text: t("captain.v2.status.manager") }
    : {
        start: { tone: "blue" as const, text: t("captain.v2.status.toStart") },
        arrive: { tone: "blue" as const, text: t("captain.status.captain_on_the_way") },
        before: { tone: "blue" as const, text: t("captain.v2.status.arrived") },
        after: { tone: "amber" as const, text: t("captain.v2.status.washing") },
        done: pay.paid ? { tone: "green" as const, text: t("captain.v2.status.done") } : { tone: "amber" as const, text: t("captain.v2.status.payDue") },
        closed: { tone: "gray" as const, text: t("captain.status.cancelled") },
      }[step];

  // "Sedan · Star Wash" / "2 × Sedan · Star Wash" — car type, then service.
  const what = visitCarService(slab.bookings, types);

  return (
    <Link
      to={`/captain/jobs/${car.id}`}
      className="flex min-h-[76px] items-center gap-3 rounded-[16px] border border-[#E4E9F1] bg-white px-3.5 py-3 active:bg-[#EEF3FA]"
    >
      <div className="w-[68px] shrink-0">
        <p className="tabular-nums text-[15px] font-extrabold text-[#0E1A33]">
          {done ? clock(car.completed_at) : formatTime12(car.scheduled_slot.split("-")[0])}
        </p>
        <p className="text-xs font-semibold text-[#5F6878]">
          {day === "Today" ? t("captain.v2.today") : day === "Tomorrow" ? t("captain.v2.tomorrow") : day}
        </p>
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-bold text-[#0E1A33]">{what}</p>
        <p className="truncate text-[13px] text-[#5F6878]">
          {done
            ? pay.paid
              ? rupees(slab.totalAmount)
              : t("captain.collect.title").replace("{amount}", rupees(pay.due))
            : car.address_snapshot?.line1 || car.customer_name || ""}
        </p>
      </div>
      <Pill tone={status.tone}>{status.text}</Pill>
      <ChevronRight className="h-5 w-5 shrink-0 text-[#A3AAB6]" />
    </Link>
  );
}
