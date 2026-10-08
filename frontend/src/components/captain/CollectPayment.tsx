/**
 * Collect at the end (founder spec 1.5): once the visit is done, if it
 * still owes money the captain sees "Collect ₹X" and two choices —
 *
 *   Cash   → "I Collected ₹X Cash" → POST /payments/collect/cash quoting X
 *            (409 AMOUNT_DUE_CHANGED → refetch, show the new amount; a
 *            wallet_credit in the answer = the customer also paid online).
 *   Online → Razorpay QR for exactly what's due, polled until paid.
 *
 * The visit's status is polled the whole time, so a customer paying from
 * their own phone hides the choices. Nothing due → "Fully Paid". Prepaid
 * with nothing paid yet → no Cash (the backend refuses it too). Every
 * amount is the server's amount_due; the backend answers for the whole
 * visit, so a multi-car visit pays once.
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { Banknote, CheckCircle2, QrCode, RefreshCw, Wallet } from "lucide-react";
import { captainOnsiteApi, type CollectState } from "../../api/captainOnsite";
import { getErrorCode, getErrorMessage } from "../../lib/api-client";
import { translateCaptainError } from "../../lib/captainErrorTranslations";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import type { Booking } from "../../types";
import axios from "axios";
import { paymentOf, rupees } from "./jobState";
import { Btn, Notice, Panel } from "./ui";

type Mode = "choose" | "cash" | "qr";

export function CollectPayment({ cars, anchor }: { cars: Booking[]; anchor: Booking }) {
  const { t, language } = useCaptainTranslation();
  const queryClient = useQueryClient();
  const pay = paymentOf(cars);
  const [mode, setMode] = useState<Mode>("choose");
  const [qr, setQr] = useState<string | null>(null);
  const [changed, setChanged] = useState(false);
  const [error, setError] = useState("");
  const [cashDone, setCashDone] = useState<CollectState | null>(null);
  const [paidLive, setPaidLive] = useState(false);
  // A fast double tap must not open AND confirm the cash in one go.
  const openedAt = useRef(0);

  const owes = pay.due > 0.004;
  const live = useQuery({
    queryKey: ["collect-status", anchor.id],
    queryFn: () => captainOnsiteApi.collectStatus(anchor.id),
    enabled: owes && !cashDone,
    // Fast while the QR is up; slower otherwise, so a payment the customer
    // makes from their own phone still clears the screen.
    refetchInterval: mode === "qr" ? 4000 : 15000,
  });

  const refreshAll = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["my-jobs"] }),
      queryClient.invalidateQueries({ queryKey: ["captain-visit"] }),
      queryClient.invalidateQueries({ queryKey: ["my-wallet"] }),
    ]);

  useEffect(() => {
    if (live.data?.payment_status === "paid" && owes) {
      setPaidLive(true);
      void refreshAll();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.data?.payment_status]);

  // What is due now: the live answer when it says something is owed, else the cars.
  const due = live.data && live.data.payment_status !== "paid" && (live.data.amount ?? 0) > 0 ? (live.data.amount as number) : pay.due;
  // The amount on screen is frozen when Cash is opened — that is what he quotes.
  const [quoted, setQuoted] = useState(0);
  // The due moved while the cash confirm was open (paid online, an add-on):
  // back to the choices with the new amount — never confirm a stale one.
  useEffect(() => {
    if (mode === "cash" && !cash.isPending && Math.abs(due - quoted) > 0.004) {
      setMode("choose");
      setChanged(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [due]);

  const fail = (e: unknown) => setError(axios.isAxiosError(e) ? translateCaptainError(getErrorMessage(e), language) : getErrorMessage(e));

  const cash = useMutation({
    mutationFn: (amount: number) => captainOnsiteApi.collectCash(anchor.id, amount),
    onMutate: () => setError(""),
    onSuccess: async (res) => {
      setCashDone(res);
      await refreshAll();
      queryClient.invalidateQueries({ queryKey: ["collect-status", anchor.id] });
    },
    onError: async (e) => {
      if (getErrorCode(e) === "AMOUNT_DUE_CHANGED") {
        setChanged(true);
        setMode("choose");
        await Promise.all([live.refetch(), refreshAll()]);
        return;
      }
      fail(e);
    },
  });
  const link = useMutation({
    mutationFn: () => captainOnsiteApi.collectLink(anchor.id),
    onMutate: () => setError(""),
    onSuccess: async (res) => {
      setQr(await QRCode.toDataURL(res.short_url, { width: 280, margin: 1 }));
      setMode("qr");
    },
    onError: fail,
  });

  if (cashDone) {
    const credit = Number(cashDone.wallet_credit) || 0;
    return (
      <div className="space-y-2" data-testid="captain-collect-done">
        <Notice tone="green" icon={<CheckCircle2 className="h-4 w-4" />}>
          <span className="font-bold">{t("captain.collect.cashDone").replace("{amount}", rupees(cashDone.amount ?? quoted))}</span>
        </Notice>
        {credit > 0 && (
          <Notice tone="blue" icon={<Wallet className="h-4 w-4" />}>
            {t("captain.collect.walletCredit").replace("{amount}", rupees(credit))}
          </Notice>
        )}
      </div>
    );
  }
  if (paidLive || (mode === "qr" && live.data?.payment_status === "paid")) {
    return (
      <div data-testid="captain-collect-done">
        <Notice tone="green" icon={<CheckCircle2 className="h-4 w-4" />}>
          <span className="font-bold">{t("captain.collect.paid")}</span>
        </Notice>
      </div>
    );
  }
  if (!owes) {
    return (
      <div data-testid="captain-collect-done">
        <Notice tone="green" icon={<CheckCircle2 className="h-4 w-4" />}>
          {pay.plan ? t("captain.v2.pay.plan") : <span className="font-bold">{t("captain.money.fullyPaid")}</span>}
        </Notice>
      </div>
    );
  }

  return (
    <Panel className="p-4">
      <div data-testid="captain-collect">
        <p className="tabular-nums text-[28px] font-extrabold leading-tight text-[#0E1A33]" data-testid="captain-collect-amount">
          {t("captain.collect.title").replace("{amount}", rupees(mode === "cash" ? quoted : due))}
        </p>
        {changed && (
          <div className="mt-2">
            <Notice tone="amber" icon={<RefreshCw className="h-4 w-4" />}>{t("captain.collect.changed")}</Notice>
          </div>
        )}
        {pay.prepaid && mode !== "qr" && <p className="mt-1 text-sm font-semibold text-[#9A6400]">{t("captain.v2.pay.prepaidNoCash")}</p>}

        {mode === "choose" && (
          <div className={`mt-3 grid gap-2 ${pay.prepaid ? "grid-cols-1" : "grid-cols-2"}`}>
            {!pay.prepaid && (
              <Btn
                variant="outline"
                className="min-h-[64px] text-[16px]"
                disabled={cash.isPending || link.isPending}
                onClick={() => {
                  openedAt.current = Date.now();
                  setQuoted(due);
                  setError("");
                  setChanged(false);
                  setMode("cash");
                }}
              >
                <Banknote className="h-5 w-5" /> {t("captain.collect.cash")}
              </Btn>
            )}
            <Btn
              variant="secondary"
              className="min-h-[64px] text-[16px]"
              loading={link.isPending}
              disabled={cash.isPending}
              onClick={() => {
                setChanged(false);
                link.mutate();
              }}
            >
              {!link.isPending && <QrCode className="h-5 w-5" />} {t("captain.collect.online")}
            </Btn>
          </div>
        )}

        {mode === "cash" && (
          <div className="mt-3 space-y-2">
            <p className="text-sm font-medium text-[#5F6878]">{t("captain.collect.cashHint")}</p>
            <Btn
              variant="success"
              className="min-h-[60px] w-full text-[16px]"
              loading={cash.isPending}
              onClick={() => {
                if (cash.isPending || Date.now() - openedAt.current < 600) return;
                cash.mutate(quoted);
              }}
              data-testid="captain-collect-cash-confirm"
            >
              {!cash.isPending && <Banknote className="h-5 w-5" />}
              {t("captain.collect.confirmCash").replace("{amount}", rupees(quoted))}
            </Btn>
            <Btn variant="outline" className="w-full" disabled={cash.isPending} onClick={() => setMode("choose")}>
              {t("captain.common.back")}
            </Btn>
          </div>
        )}

        {mode === "qr" && qr && (
          <div className="mt-3 text-center">
            <p className="text-sm font-semibold text-[#5F6878]">{t("captain.collect.scan").replace("{amount}", rupees(due))}</p>
            <img src={qr} alt="Payment QR" className="mx-auto mt-2 h-60 w-60 rounded-2xl border border-[#E4E9F1]" />
            <p className="mt-2 flex items-center justify-center gap-2 text-sm font-semibold text-[#5F6878]">
              <span className="h-2 w-2 animate-pulse rounded-full bg-[#0A66F0]" /> {t("captain.payment.waiting_payment")}
            </p>
            <Btn variant="outline" className="mt-2 w-full" onClick={() => setMode("choose")}>
              {t("captain.common.back")}
            </Btn>
          </div>
        )}

        {error && (
          <p className="mt-2 text-sm font-medium text-[#B91C1C]" role="alert">
            {error}
          </p>
        )}
      </div>
    </Panel>
  );
}
