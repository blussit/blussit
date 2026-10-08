/**
 * Captain · Earnings — today and this week, then the recent finished jobs.
 * Per-job fees and the wallet only show while wallet gating is on (admin
 * pricing toggle); otherwise it's the work count and the cash he holds.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpCircle, Landmark, TriangleAlert } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { bookingPolicyApi } from "../../api/catalog";
import { walletApi } from "../../api/wallet";
import { getErrorMessage } from "../../lib/api-client";
import { todayIST, formatDay } from "../../lib/date";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { carService, clock, istDay, rupees, statusWord, useCarTypes, weekStartIST } from "../../components/captain/jobState";
import { Btn, LoadError, Notice, PageTitle, Panel, Pill, Sheet } from "../../components/captain/ui";
import type { Booking } from "../../types";

const field =
  "h-12 w-full rounded-2xl border border-[#E4E9F1] px-4 text-[15px] text-[#0E1A33] outline-none focus:border-[#0A66F0] focus:ring-2 focus:ring-[#E8F0FE]";

export default function CaptainEarningsPage() {
  const { t } = useCaptainTranslation();
  const queryClient = useQueryClient();
  const policyQuery = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });
  const policy = policyQuery.data;
  const walletOn = !!policy?.wallet_gating_enabled;
  const recent = useQuery({
    queryKey: ["my-jobs", "history", "recent"],
    queryFn: () => bookingApi.myJobs({ scope: "history", status: "completed", page: 1, page_size: 100 }),
  });
  const walletQuery = useQuery({ queryKey: ["my-wallet"], queryFn: walletApi.myWallet, enabled: walletOn });
  const wallet = walletQuery.data;
  const withdrawalsQuery = useQuery({
    queryKey: ["my-withdrawals"],
    queryFn: () => walletApi.myWithdrawals({ page: 1, page_size: 5 }),
    enabled: walletOn,
  });

  const [sheet, setSheet] = useState<null | "withdraw" | "bank">(null);
  const [amount, setAmount] = useState("");
  const [bank, setBank] = useState({ bank_account_holder: "", bank_account_number: "", bank_ifsc: "" });
  const [formError, setFormError] = useState("");
  const close = () => {
    setSheet(null);
    setFormError("");
  };
  const withdraw = useMutation({
    mutationFn: () => walletApi.requestWithdrawal(Number(amount)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-wallet"] });
      queryClient.invalidateQueries({ queryKey: ["my-withdrawals"] });
      setAmount("");
      close();
    },
    onError: (e) => setFormError(getErrorMessage(e)),
  });
  const saveBank = useMutation({
    mutationFn: () => walletApi.updateBankDetails(bank),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-wallet"] });
      close();
    },
    onError: (e) => setFormError(getErrorMessage(e)),
  });

  const withdrawals = withdrawalsQuery.data;

  // Failed reads are said plainly — never ₹0 tiles, "no finished jobs", or a
  // wallet that silently isn't there.
  const recentFailed = recent.isError && !recent.data;
  // Without the policy we can't tell whether fees/the wallet apply, so the
  // tiles would guess; retry it alongside the jobs.
  const policyFailed = policyQuery.isError && !policy;
  const walletFailed = walletOn && walletQuery.isError && !wallet;
  const withdrawalsFailed = walletOn && withdrawalsQuery.isError && !withdrawals;
  const retryLabel = t("captain.v2.tryAgain");
  const retrySub = t("captain.v2.loadFailedSub");

  const jobs = (recent.data?.data ?? []).filter((j) => j.completed_at);
  const types = useCarTypes(jobs.slice(0, 10));
  const today = todayIST();
  const week = weekStartIST();
  const sum = (list: Booking[]) => ({
    count: list.length,
    fee: list.reduce((s, j) => s + (j.captain_earning ?? 0), 0),
    cash: list.filter((j) => j.payment_method === "cash").reduce((s, j) => s + (j.total_amount ?? 0), 0),
  });
  const todayStats = sum(jobs.filter((j) => istDay(j.completed_at) === today));
  const weekStats = sum(jobs.filter((j) => istDay(j.completed_at) >= week));
  const jobsWord = (n: number) => t(n === 1 ? "captain.v2.oneJob" : "captain.v2.nJobs").replace("{n}", String(n));

  const tile = (label: string, s: ReturnType<typeof sum>) => (
    <Panel className="px-4 py-4">
      <p className="text-[13px] font-bold text-[#5F6878]">{label}</p>
      <p className="mt-1 tabular-nums text-[28px] font-extrabold leading-none text-[#0E1A33]">{walletOn ? rupees(s.fee) : jobsWord(s.count)}</p>
      <p className="mt-1.5 text-sm font-semibold text-[#5F6878]">
        {walletOn ? jobsWord(s.count) : t("captain.v2.cashHeld").replace("{amount}", rupees(s.cash))}
      </p>
    </Panel>
  );

  const below = wallet ? wallet.balance < wallet.minimum_balance : false;

  return (
    <div className="space-y-4">
      <PageTitle>{t("captain.v2.tab.earnings")}</PageTitle>
      {recentFailed || policyFailed ? (
        <LoadError
          title="Couldn't Load Your Earnings"
          sub={retrySub}
          retryLabel={retryLabel}
          busy={recent.isFetching || policyQuery.isFetching}
          onRetry={() => {
            if (recentFailed) void recent.refetch();
            if (policyFailed) void policyQuery.refetch();
          }}
        />
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {tile(t("captain.v2.today"), todayStats)}
          {tile(t("captain.v2.thisWeek"), weekStats)}
        </div>
      )}

      {walletFailed && (
        <LoadError title="Couldn't Load Your Wallet" sub={retrySub} retryLabel={retryLabel} busy={walletQuery.isFetching} onRetry={() => void walletQuery.refetch()} />
      )}

      {walletOn && wallet && (
        <Panel className="p-4">
          <div className="flex items-end justify-between gap-3">
            <div>
              <p className="text-[13px] font-bold text-[#5F6878]">{t("captain.v2.walletBalance")}</p>
              <p className="tabular-nums text-[28px] font-extrabold text-[#0E1A33]">{rupees(wallet.balance)}</p>
            </div>
            <p className="text-right text-xs font-semibold text-[#5F6878]">
              {t("captain.earnings.minimumRequired")}
              <span className="block tabular-nums text-sm text-[#0E1A33]">{rupees(wallet.minimum_balance)}</span>
            </p>
          </div>
          {below && (
            <div className="mt-3">
              <Notice tone="amber" icon={<TriangleAlert className="h-4 w-4" />}>{t("captain.v2.belowMin")}</Notice>
            </div>
          )}
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Btn variant="secondary" onClick={() => setSheet("withdraw")}>
              <ArrowUpCircle className="h-5 w-5" /> {t("captain.v2.withdraw")}
            </Btn>
            <Btn
              variant="outline"
              onClick={() => {
                setBank({
                  bank_account_holder: wallet.bank_account_holder || "",
                  bank_account_number: wallet.bank_account_number || "",
                  bank_ifsc: wallet.bank_ifsc || "",
                });
                setSheet("bank");
              }}
            >
              <Landmark className="h-5 w-5" /> {t("captain.earnings.bankDetails")}
            </Btn>
          </div>
          {withdrawalsFailed && (
            <p role="alert" className="mt-3 border-t border-[#E4E9F1] pt-3 text-sm text-[#5F6878]">
              Couldn't load your withdrawals.{" "}
              <button
                type="button"
                className="font-bold text-[#0A66F0] disabled:opacity-60"
                disabled={withdrawalsQuery.isFetching}
                onClick={() => void withdrawalsQuery.refetch()}
              >
                {retryLabel}
              </button>
            </p>
          )}
          {!!withdrawals?.data.length && (
            <ul className="mt-3 divide-y divide-[#E4E9F1] border-t border-[#E4E9F1]">
              {withdrawals.data.map((w) => (
                <li key={w.id} className="flex items-center justify-between py-2.5 text-sm">
                  <span className="tabular-nums font-bold text-[#0E1A33]">{rupees(w.amount)}</span>
                  <Pill tone={w.status === "paid" || w.status === "approved" ? "green" : w.status === "rejected" ? "red" : "amber"}>{statusWord(w.status)}</Pill>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      {!recentFailed && (
      <div>
        <h2 className="mb-2 text-[15px] font-extrabold text-[#0E1A33]">{t("captain.v2.recentJobs")}</h2>
        {recent.isLoading ? (
          <Panel className="h-40 animate-pulse bg-[#EEF3FA]"><span /></Panel>
        ) : !jobs.length ? (
          <Panel className="px-4 py-8 text-center text-sm font-semibold text-[#5F6878]">{t("captain.v2.noDone")}</Panel>
        ) : (
          <Panel className="divide-y divide-[#E4E9F1]">
            {jobs.slice(0, 10).map((j) => {
              const day = formatDay(istDay(j.completed_at));
              return (
                <Link key={j.id} to={`/captain/jobs/${j.id}`} className="flex min-h-[60px] items-center gap-3 px-4 py-2.5 active:bg-[#EEF3FA]">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[15px] font-bold text-[#0E1A33]">{carService(j, types)}</p>
                    <p className="text-[13px] text-[#5F6878]">
                      {day === "Today" ? t("captain.v2.today") : day === "Tomorrow" ? t("captain.v2.tomorrow") : day} · {clock(j.completed_at)}
                      {!walletOn && ` · ${j.payment_method === "cash" ? t("captain.v2.cash") : j.payment_method === "subscription" ? t("captain.v2.plan") : t("captain.v2.online")}`}
                    </p>
                  </div>
                  <span className="tabular-nums text-[16px] font-extrabold text-[#0E1A33]">
                    {walletOn ? rupees(j.captain_earning) : rupees(j.total_amount)}
                  </span>
                </Link>
              );
            })}
          </Panel>
        )}
      </div>
      )}

      <Sheet open={sheet === "withdraw"} onClose={close} title={t("captain.earnings.requestWithdrawal")}>
        <input
          className={field}
          type="number"
          inputMode="numeric"
          min={1}
          value={amount}
          placeholder={t("captain.earnings.amount")}
          onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))}
        />
        {formError && <p className="mt-2 text-sm font-medium text-[#B91C1C]">{formError}</p>}
        <Btn className="mt-3 w-full" disabled={!amount || Number(amount) <= 0} loading={withdraw.isPending} onClick={() => withdraw.mutate()}>
          {t("captain.common.submit")}
        </Btn>
      </Sheet>

      <Sheet open={sheet === "bank"} onClose={close} title={t("captain.earnings.bankDetails")}>
        <div className="space-y-2">
          <input className={field} placeholder={t("captain.earnings.accountHolder")} value={bank.bank_account_holder} onChange={(e) => setBank((b) => ({ ...b, bank_account_holder: e.target.value }))} />
          <input className={field} inputMode="numeric" placeholder={t("captain.earnings.accountNumber")} value={bank.bank_account_number} onChange={(e) => setBank((b) => ({ ...b, bank_account_number: e.target.value }))} />
          <input className={field} placeholder={t("captain.earnings.ifsc")} value={bank.bank_ifsc} onChange={(e) => setBank((b) => ({ ...b, bank_ifsc: e.target.value.toUpperCase() }))} />
        </div>
        {formError && <p className="mt-2 text-sm font-medium text-[#B91C1C]">{formError}</p>}
        <Btn
          className="mt-3 w-full"
          disabled={!bank.bank_account_holder || !bank.bank_account_number || !bank.bank_ifsc}
          loading={saveBank.isPending}
          onClick={() => saveBank.mutate()}
        >
          {t("captain.common.save")}
        </Btn>
      </Sheet>
    </div>
  );
}
