import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { Banknote, CheckCircle2, ChevronLeft, QrCode } from "lucide-react";
import { paymentApi } from "../../api/payment";
import { Modal } from "../ui";
import { getErrorMessage } from "../../lib/api-client";
import type { Booking } from "../../types";

/**
 * The captain's doorstep settlement — founder spec, kept deliberately
 * simple: when the wash is done and the booking is unpaid, two big
 * buttons — 💵 cash received, or 📱 show a QR the customer scans and
 * pays. A booking the customer already paid online just shows a ✅.
 * While the QR is on screen, the modal polls the backend (which asks
 * Razorpay directly) so the ✅ lands seconds after the customer pays.
 */
export function CollectPaymentModal({ booking, onClose }: { booking: Booking | null; onClose: () => void }) {
  const { t } = useCaptainTranslation();
  const queryClient = useQueryClient();
  const [view, setView] = useState<"choose" | "qr">("choose");
  const [cashArmed, setCashArmed] = useState(false);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [error, setError] = useState("");

  // Live payment state — checked on open (customer may have prepaid) and
  // every 4s while the QR is showing.
  const { data: live } = useQuery({
    queryKey: ["collect-status", booking?.id],
    queryFn: () => paymentApi.collectStatus(booking!.id),
    enabled: !!booking,
    refetchInterval: view === "qr" ? 4000 : false,
  });
  const paid = live?.payment_status === "paid";

  useEffect(() => {
    if (!booking) {
      setView("choose");
      setCashArmed(false);
      setQrDataUrl(null);
      setError("");
    }
  }, [booking]);

  useEffect(() => {
    if (paid) queryClient.invalidateQueries({ queryKey: ["my-jobs"] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paid]);

  const cashMutation = useMutation({
    mutationFn: () => paymentApi.collectCash(booking!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-jobs"] });
      queryClient.invalidateQueries({ queryKey: ["collect-status", booking?.id] });
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const qrMutation = useMutation({
    mutationFn: () => paymentApi.collectLink(booking!.id),
    onSuccess: async (link) => {
      setQrDataUrl(await QRCode.toDataURL(link.short_url, { width: 280, margin: 1 }));
      setView("qr");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (!booking) return null;
  const amount = booking.total_amount;
  const settled = paid || cashMutation.isSuccess;

  return (
    <Modal open={!!booking} onClose={onClose} title={settled ? t("captain.payment.done") : `${t("captain.payment.amount")} ₹${amount}`}>
      {settled ? (
        <div className="py-6 text-center">
          <CheckCircle2 className="mx-auto h-14 w-14 text-[var(--color-success)]" />
          <p className="mt-3 text-lg font-bold text-black">
            ₹{amount} {cashMutation.isSuccess || live?.payment_method === "cash" ? t("captain.payment.received_cash") : t("captain.payment.paid_online")} ✅
          </p>
          <p className="mt-1 text-sm text-gray-600">
            {t("captain.payment.settled_desc").replace("{booking_number}", booking.booking_number)}
          </p>
          <button onClick={onClose} className="mt-5 w-full rounded-full bg-black py-3 text-sm font-semibold text-white">{t("captain.common.close")}</button>
        </div>
      ) : view === "qr" ? (
        <div className="text-center">
          <p className="text-sm text-gray-600">{t("captain.payment.scan_pay_desc")}</p>
          <p className="mt-1 font-mono-num text-2xl font-bold text-black">₹{amount}</p>
          {qrDataUrl && <img src={qrDataUrl} alt="Payment QR" className="mx-auto mt-3 h-64 w-64 rounded-xl border border-[#F3E5B5]" />}
          <p className="mt-3 flex items-center justify-center gap-2 text-xs text-gray-400">
            <span className="h-2 w-2 animate-pulse rounded-full bg-[#E8A900]" /> {t("captain.payment.waiting_payment")}
          </p>
          <button
            onClick={() => setView("choose")}
            className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-gray-600 hover:text-black"
          >
            <ChevronLeft className="h-4 w-4" /> {t("captain.common.back")}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-gray-600">
            {t("captain.payment.service_done_desc")
              .replace("{amount}", `₹${amount}`)
              .replace("{customer}", booking.customer_name || "the customer")}
          </p>
          <button
            onClick={() => (cashArmed ? cashMutation.mutate() : setCashArmed(true))}
            disabled={cashMutation.isPending}
            className={`flex w-full items-center justify-center gap-2 rounded-2xl border-2 py-4 text-base font-bold transition-colors ${
              cashArmed ? "border-[var(--color-success)] bg-green-50 text-green-800" : "border-[#F3E5B5] bg-white text-black hover:border-black"
            }`}
          >
            <Banknote className="h-5 w-5" />
            {cashArmed ? t("captain.payment.tap_again_cash").replace("{amount}", `₹${amount}`) : t("captain.payment.cash")}
          </button>
          <button
            onClick={() => {
              setCashArmed(false);
              qrMutation.mutate();
            }}
            disabled={qrMutation.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-2xl bg-[#E8A900] py-4 text-base font-bold text-white hover:bg-[#D99A00]"
          >
            <QrCode className="h-5 w-5" />
            {qrMutation.isPending ? t("captain.payment.preparing_qr") : t("captain.payment.qr")}
          </button>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        </div>
      )}
    </Modal>
  );
}
