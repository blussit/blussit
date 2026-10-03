/**
 * Doorstep settlement on the "Booking completed" screen (founder spec):
 * nothing owed → a ✓; owed → "Cash received" (tap twice) or "Show QR".
 * A prepaid booking NEVER offers cash — the backend refuses it too. The
 * backend answers for the whole visit, so a multi-car visit pays once.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { Banknote, CheckCircle2, QrCode } from "lucide-react";
import { paymentApi } from "../../api/payment";
import { getErrorMessage } from "../../lib/api-client";
import { translateCaptainError } from "../../lib/captainErrorTranslations";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import type { Booking } from "../../types";
import { paymentOf, rupees } from "./jobState";
import { Btn, Notice, Panel } from "./ui";

export function CollectPayment({ cars, anchor }: { cars: Booking[]; anchor: Booking }) {
  const { t, language } = useCaptainTranslation();
  const queryClient = useQueryClient();
  const pay = paymentOf(cars);
  const [qr, setQr] = useState<string | null>(null);
  const [cashArmed, setCashArmed] = useState(false);
  const [error, setError] = useState("");

  const live = useQuery({
    queryKey: ["collect-status", anchor.id],
    queryFn: () => paymentApi.collectStatus(anchor.id),
    enabled: !pay.paid,
    refetchInterval: qr ? 4000 : false,
  });
  const settled = pay.paid || live.data?.payment_status === "paid";

  useEffect(() => {
    if (live.data?.payment_status === "paid") {
      queryClient.invalidateQueries({ queryKey: ["my-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["captain-visit"] });
    }
  }, [live.data?.payment_status, queryClient]);

  const cash = useMutation({
    mutationFn: () => paymentApi.collectCash(anchor.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["captain-visit"] });
      queryClient.invalidateQueries({ queryKey: ["collect-status", anchor.id] });
    },
    onError: (e) => setError(translateCaptainError(getErrorMessage(e), language)),
  });
  const link = useMutation({
    mutationFn: () => paymentApi.collectLink(anchor.id),
    onSuccess: async (res) => setQr(await QRCode.toDataURL(res.short_url, { width: 280, margin: 1 })),
    onError: (e) => setError(translateCaptainError(getErrorMessage(e), language)),
  });

  if (pay.plan && pay.paid) {
    return <Notice tone="green" icon={<CheckCircle2 className="h-4 w-4" />}>{t("captain.v2.pay.plan")}</Notice>;
  }
  if (settled || cash.isSuccess) {
    const byCash = cash.isSuccess || cars.some((c) => c.payment_method === "cash");
    return (
      <Notice tone="green" icon={<CheckCircle2 className="h-4 w-4" />}>
        {(byCash ? t("captain.v2.pay.cashDone") : t("captain.v2.pay.onlineDone")).replace("{amount}", rupees(pay.total))}
      </Notice>
    );
  }

  const due = live.data?.amount && live.data.amount > 0 ? live.data.amount : pay.due;
  return (
    <Panel className="p-4" >
      <p className="text-sm font-semibold text-[#5F6878]">{t("captain.v2.pay.collect")}</p>
      <p className="tabular-nums text-3xl font-extrabold text-[#0E1A33]">{rupees(due)}</p>
      {pay.prepaid && <p className="mt-1 text-sm font-semibold text-[#9A6400]">{t("captain.v2.pay.prepaidNoCash")}</p>}
      {qr ? (
        <div className="mt-3 text-center">
          <img src={qr} alt="Payment QR" className="mx-auto h-60 w-60 rounded-2xl border border-[#E4E9F1]" />
          <p className="mt-2 flex items-center justify-center gap-2 text-sm font-semibold text-[#5F6878]">
            <span className="h-2 w-2 animate-pulse rounded-full bg-[#0A66F0]" /> {t("captain.payment.waiting_payment")}
          </p>
          <button type="button" onClick={() => setQr(null)} className="mt-2 min-h-[44px] text-sm font-bold text-[#0A66F0]">
            {t("captain.common.back")}
          </button>
        </div>
      ) : (
        <div className="mt-3 grid gap-2">
          {!pay.prepaid && (
            <Btn
              variant={cashArmed ? "success" : "outline"}
              loading={cash.isPending}
              onClick={() => (cashArmed ? cash.mutate() : setCashArmed(true))}
            >
              <Banknote className="h-5 w-5" />
              {cashArmed ? t("captain.payment.tap_again_cash").replace("{amount}", rupees(due)) : t("captain.payment.cash")}
            </Btn>
          )}
          <Btn
            variant="secondary"
            loading={link.isPending}
            onClick={() => {
              setCashArmed(false);
              setError("");
              link.mutate();
            }}
          >
            <QrCode className="h-5 w-5" /> {t("captain.v2.pay.showQr")}
          </Btn>
        </div>
      )}
      {error && <p className="mt-2 text-sm font-medium text-[#B91C1C]">{error}</p>}
    </Panel>
  );
}
