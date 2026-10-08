/**
 * Pass extension (founder rule 2026-10-07) — ANY pass now (society, monthly,
 * custom multi-car), not only society. A pass is a 30-day period; once it
 * ends its remaining washes are frozen unless a manager (own center) or an
 * admin extends it by a few days — at most 10 in total per period, only in
 * its last 3 days or after it ended, while washes remain. Every grant is
 * recorded (who, when, how many days) and shown to admin.
 */
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarPlus } from "lucide-react";
import { societyApi } from "../../api/society";
import { passApi } from "../../api/customPlans";
import { getErrorMessage } from "../../lib/api-client";
import { formatDateTime, formatShortDate } from "../../lib/date";
import { toTitle } from "../../lib/titleCase";
import { useToast } from "../../context/ToastContext";
import { Button, Input, Modal } from "../ui";
import type { PassExtension } from "../../types";
import { lastBookingDayText } from "../customer/passDates";

/** "12 Oct" from a YYYY-MM-DD day, or the IST day of an ISO instant. */
export const dayLabel = (value?: string | null) => {
  if (!value) return "";
  if (value.length <= 10) return formatShortDate(value);
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? formatShortDate(value) : formatShortDate(new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(d));
};

/** The last day a booking can be dated on for a pass usable until
 *  `instant` (bookings dated before the day it ends) — the same "book
 *  until" day the server's last_bookable_day gives. */
export const lastBookableLabel = (instant?: string | null) => {
  if (!instant) return "";
  const d = new Date(instant);
  if (Number.isNaN(d.getTime())) return dayLabel(instant);
  const key = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(d);
  const prev = new Date(`${key}T12:00:00Z`);
  prev.setUTCDate(prev.getUTCDate() - 1);
  return formatShortDate(prev.toISOString().slice(0, 10));
};

/** "Extended +3 Days (Until 12 Oct)" — the line every staff pass view shows. */
export function extensionLine(days?: number | null, extendedUntil?: string | null): string {
  if (!days || days <= 0) return "";
  const until = extendedUntil ? lastBookableLabel(extendedUntil) : "";
  return `Extended +${days} Day${days === 1 ? "" : "s"}${until ? ` (Until ${until})` : ""}`;
}

export function ExtensionHistory({ extensions }: { extensions?: PassExtension[] | null }) {
  if (!extensions?.length) return null;
  return (
    <ul className="space-y-1.5" data-testid="extension-history">
      {[...extensions].reverse().map((e, i) => (
        <li key={i} className="rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs text-gray-600">
          <span className="font-semibold text-[#0E1A33]">+{e.days} Day{e.days === 1 ? "" : "s"}</span>
          {e.extended_until ? ` · book until ${lastBookableLabel(e.extended_until)}` : ""}
          {e.by_name ? ` · ${e.by_name}${e.role ? ` (${toTitle(e.role)})` : ""}` : ""}
          {e.at ? ` · ${formatDateTime(e.at)}` : ""}
          {e.note ? <span className="block text-gray-500">&ldquo;{e.note}&rdquo;</span> : null}
        </li>
      ))}
    </ul>
  );
}

export interface ExtendTarget {
  /** A society pass goes through its society's route; any other pass
   *  through POST /subscriptions/{id}/extend. */
  societyId?: string | null;
  subscriptionId: string;
  /** Plate, or "Hatchback · Star Wash" when the pass has no car. */
  plate: string;
  /** Plan name for the dialog's first line (non-society passes). */
  planName?: string | null;
  remaining: number;
  endDate?: string | null;
  daysUsed: number;
  daysLeft: number;
  extensions?: PassExtension[];
}

export function ExtendPassDialog({ target, onClose }: { target: ExtendTarget | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [days, setDays] = useState("3");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!target) return;
    setDays(String(Math.min(3, Math.max(1, target.daysLeft))));
    setNote("");
    setError("");
  }, [target]);

  const n = Math.round(Number(days));
  const valid = Number.isFinite(n) && n >= 1 && n <= (target?.daysLeft ?? 0);
  const save = useMutation({
    mutationFn: (): Promise<{ last_bookable_day?: string | null; last_booking_day_label?: string | null }> =>
      target!.societyId
        ? societyApi.extendPass(target!.societyId, target!.subscriptionId, n, note.trim() || undefined)
        : passApi.extend(target!.subscriptionId, n, note.trim() || undefined),
    onSuccess: (r) => {
      if (target!.societyId) queryClient.invalidateQueries({ queryKey: ["society-enrollments", target!.societyId] });
      for (const key of ["admin-subscriptions-overview", "center-subscription-overview", "customer-360", "custom-plans", "custom-plan"]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      pushToast({
        tone: "success",
        title: `Extended By ${n} Day${n === 1 ? "" : "s"}`,
        // "Last Booking Day: 6 Nov 2026" — the words every pass screen uses.
        message: lastBookingDayText({ last_booking_day_label: r.last_booking_day_label, last_bookable_day: r.last_bookable_day }) || undefined,
      });
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <Modal open={!!target} onClose={onClose} title="Extend Pass" maxWidth="max-w-sm">
      {target && (
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            {target.planName ? <span className="font-semibold text-[#0E1A33]">{target.planName} · </span> : null}
            <span className="font-mono font-semibold text-[#0E1A33]">{target.plate}</span> has{" "}
            <span className="font-semibold text-[#0E1A33]">
              {target.remaining} {target.societyId ? "premium " : ""}wash{target.remaining === 1 ? "" : "es"}
            </span>{" "}
            left{target.endDate ? ` and its 30 days end${new Date(target.endDate).getTime() < Date.now() ? "ed" : ""} ${dayLabel(target.endDate)}` : ""}.{" "}
            {target.societyId ? "Extra days let them book those washes only — no daily washes." : "Extra days let them book those washes only."}
          </p>
          <Input
            label={`Days To Add (1–${target.daysLeft})`}
            type="number"
            inputMode="numeric"
            min={1}
            max={target.daysLeft}
            value={days}
            onChange={(e) => setDays(e.target.value.replace(/\D/g, "").slice(0, 2))}
            error={days !== "" && !valid ? `Between 1 and ${target.daysLeft} — at most 10 days per 30-day period.` : undefined}
            hint={target.daysUsed > 0 ? `${target.daysUsed} of 10 days already added this period.` : "At most 10 days per 30-day period."}
          />
          <Input label="Note (Optional)" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Resident was out of town" />
          <ExtensionHistory extensions={target.extensions} />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
              Back
            </Button>
            <Button className="min-h-11 flex-1" isLoading={save.isPending} disabled={!valid} onClick={() => save.mutate()}>
              <CalendarPlus className="h-4 w-4" /> Extend
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
