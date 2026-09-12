import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownCircle, ArrowUpCircle, Briefcase, IndianRupee, Landmark, Star, TrendingUp, TriangleAlert, Wallet } from "lucide-react";
import { staffDirectoryApi } from "../../api/admin";
import { bookingPolicyApi } from "../../api/catalog";
import { walletApi } from "../../api/wallet";
import { getErrorMessage } from "../../lib/api-client";
import { Badge, Button, Card, CardBody, Input, Modal, PageLoader } from "../../components/ui";
import { format } from "../../lib/date";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";

export default function CaptainEarningsPage() {
  const { t } = useCaptainTranslation();
  const queryClient = useQueryClient();
  const [showWithdraw, setShowWithdraw] = useState(false);
  const [showBank, setShowBank] = useState(false);
  const [amount, setAmount] = useState("");
  const [bank, setBank] = useState({ bank_account_number: "", bank_ifsc: "", bank_account_holder: "" });
  const [formError, setFormError] = useState<string | null>(null);

  const { data: performance, isLoading: perfLoading } = useQuery({ queryKey: ["my-performance"], queryFn: staffDirectoryApi.myPerformance });
  const { data: wallet, isLoading: walletLoading } = useQuery({ queryKey: ["my-wallet"], queryFn: walletApi.myWallet });
  const { data: policy, isLoading: policyLoading } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });
  const { data: transactions } = useQuery({
    queryKey: ["my-wallet-transactions"],
    queryFn: () => walletApi.myTransactions({ page: 1, page_size: 15 }),
  });
  const { data: withdrawals } = useQuery({
    queryKey: ["my-withdrawals"],
    queryFn: () => walletApi.myWithdrawals({ page: 1, page_size: 10 }),
  });

  const withdrawMutation = useMutation({
    mutationFn: (amt: number) => walletApi.requestWithdrawal(amt),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-wallet"] });
      queryClient.invalidateQueries({ queryKey: ["my-withdrawals"] });
      setShowWithdraw(false);
      setAmount("");
      setFormError(null);
    },
    onError: (e) => setFormError(getErrorMessage(e)),
  });

  const bankMutation = useMutation({
    mutationFn: () => walletApi.updateBankDetails(bank),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-wallet"] });
      setShowBank(false);
      setFormError(null);
    },
    onError: (e) => setFormError(getErrorMessage(e)),
  });

  if (perfLoading || walletLoading || policyLoading || !performance) return <PageLoader />;

  // Hidden while wallet balance gating is off — see AdminPricingPage's
  // toggle and CaptainLayout, which also drops this from the nav. Guards a
  // direct hit on the URL, not just the nav link.
  if (!policy?.wallet_gating_enabled) {
    return <Navigate to="/captain" replace />;
  }

  const belowMinimum = wallet ? wallet.balance < wallet.minimum_balance : false;
  const stats = [
    { label: t("captain.earnings.jobsCompleted"), value: String(performance.total_jobs_completed ?? 0), icon: Briefcase },
    { label: t("captain.earnings.averageRating"), value: String(performance.average_rating ?? 0), icon: Star },
    { label: t("captain.earnings.totalReviews"), value: String(performance.total_reviews ?? 0), icon: TrendingUp },
  ];
  const statusCounts = (performance.booking_status_counts as Record<string, number>) || {};

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">{t("captain.earnings.title")}</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Cash bookings debit the platform's share from your wallet; online bookings credit your fee straight in.
        </p>
      </div>

      <Card className="overflow-hidden">
        <div className="bg-[var(--color-primary)] px-6 py-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="flex items-center gap-2 text-sm text-white/80">
                <Wallet className="h-4 w-4" /> Available balance
              </p>
              <p className="font-mono-num mt-1 text-4xl font-bold">₹{wallet?.balance ?? 0}</p>
            </div>
            <div className="text-right text-sm text-white/80">
              <p>{t("captain.earnings.minimumRequired")}</p>
              <p className="font-mono-num font-semibold text-white">₹{wallet?.minimum_balance ?? 50}</p>
            </div>
          </div>
          {belowMinimum && (
            <div className="mt-4 flex items-center gap-2 rounded-lg bg-white/15 px-3 py-2 text-sm">
              <TriangleAlert className="h-4 w-4 shrink-0" />
              Your balance is below the minimum — hand cash to your center manager to add balance; you won't get new jobs until then.
            </div>
          )}
        </div>
        <CardBody className="flex flex-wrap gap-3">
          <Button variant="outline" onClick={() => setShowWithdraw(true)}>
            <ArrowUpCircle className="h-4 w-4" /> Request withdrawal
          </Button>
          <Button variant="outline" onClick={() => setShowBank(true)}>
            <Landmark className="h-4 w-4" /> {wallet?.bank_account_number ? t("captain.earnings.updateBankDetails") : t("captain.earnings.addBankDetails")}
          </Button>
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
        {stats.map((s) => (
          <Card key={s.label}>
            <CardBody className="flex items-center gap-4">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                <s.icon className="h-5 w-5" />
              </span>
              <div>
                <p className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{s.value}</p>
                <p className="text-sm text-[var(--color-text-secondary)]">{s.label}</p>
              </div>
            </CardBody>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardBody>
            <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">{t("captain.earnings.recentTransactions")}</h2>
            {!transactions?.data.length ? (
              <p className="text-sm text-[var(--color-text-secondary)]">{t("captain.earnings.noActivity")}</p>
            ) : (
              <div className="space-y-3">
                {transactions.data.map((t) => (
                  <div key={t.id} className="flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      {t.type === "credit" ? (
                        <ArrowDownCircle className="h-4 w-4 text-[var(--color-success)]" />
                      ) : (
                        <ArrowUpCircle className="h-4 w-4 text-[var(--color-error)]" />
                      )}
                      <div>
                        <p className="text-[var(--color-text-primary)]">{t.description}</p>
                        <p className="text-xs text-[var(--color-text-secondary)]">{format(t.created_at)}</p>
                      </div>
                    </div>
                    <span className={`font-mono-num font-semibold ${t.type === "credit" ? "text-[var(--color-success)]" : "text-[var(--color-error)]"}`}>
                      {t.type === "credit" ? "+" : "-"}₹{t.amount}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardBody>
            <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">{t("captain.earnings.withdrawalRequests")}</h2>
            {!withdrawals?.data.length ? (
              <p className="text-sm text-[var(--color-text-secondary)]">{t("captain.earnings.noWithdrawals")}</p>
            ) : (
              <div className="space-y-3">
                {withdrawals.data.map((w) => (
                  <div key={w.id} className="flex items-center justify-between text-sm">
                    <div>
                      <p className="font-mono-num font-semibold text-[var(--color-text-primary)]">₹{w.amount}</p>
                      <p className="text-xs text-[var(--color-text-secondary)]">{format(w.created_at)}</p>
                    </div>
                    <Badge tone={w.status === "paid" || w.status === "approved" ? "success" : w.status === "rejected" ? "error" : "warning"}>
                      {w.status}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardBody>
          <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">{t("captain.earnings.jobsByStatus")}</h2>
          <div className="space-y-3">
            {Object.entries(statusCounts).map(([s, count]) => (
              <div key={s} className="flex items-center justify-between text-sm">
                <span className="capitalize text-[var(--color-text-secondary)]">{s.replace(/_/g, " ")}</span>
                <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{count}</span>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>

      <Modal open={showWithdraw} onClose={() => setShowWithdraw(false)} title={t("captain.earnings.requestWithdrawal")}>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Requests are reviewed by the admin team and paid to your registered bank account.
        </p>
        <Input
          className="mt-3"
          label={t("captain.earnings.amount")}
          type="number"
          min={1}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
        {formError && <p className="mt-2 text-sm text-[var(--color-error)]">{formError}</p>}
        <Button
          className="mt-4 w-full"
          isLoading={withdrawMutation.isPending}
          disabled={!amount || Number(amount) <= 0}
          onClick={() => withdrawMutation.mutate(Number(amount))}
        >
          <IndianRupee className="h-4 w-4" /> Submit request
        </Button>
      </Modal>

      <Modal open={showBank} onClose={() => setShowBank(false)} title={t("captain.earnings.bankDetails")}>
        <div className="space-y-3">
          <Input
            label={t("captain.earnings.accountHolder")}
            value={bank.bank_account_holder}
            onChange={(e) => setBank((b) => ({ ...b, bank_account_holder: e.target.value }))}
          />
          <Input
            label={t("captain.earnings.accountNumber")}
            value={bank.bank_account_number}
            onChange={(e) => setBank((b) => ({ ...b, bank_account_number: e.target.value }))}
          />
          <Input label={t("captain.earnings.ifsc")} value={bank.bank_ifsc} onChange={(e) => setBank((b) => ({ ...b, bank_ifsc: e.target.value }))} />
        </div>
        {formError && <p className="mt-2 text-sm text-[var(--color-error)]">{formError}</p>}
        <Button
          className="mt-4 w-full"
          isLoading={bankMutation.isPending}
          disabled={!bank.bank_account_holder || !bank.bank_account_number || !bank.bank_ifsc}
          onClick={() => bankMutation.mutate()}
        >
          Save bank details
        </Button>
      </Modal>
    </div>
  );
}
