import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Undo2 } from "lucide-react";
import { customPlanApi, type CustomPlan, type CustomPlanCar } from "../../api/customPlans";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { toTitle } from "../../lib/titleCase";
import { Button, Input, Modal } from "../ui";

/**
 * Refund ONE car of a paid custom plan (PLANS-2) to the customer's wallet:
 * at most its unused washes at the per-wash price paid (`refundable_amount`;
 * a skipped car: its whole share). Staff may refund less; a reason is
 * required. That car's pass is cancelled. The customer can then ask for the
 * money back (a payback).
 */

export interface RefundTarget {
  plan: CustomPlan;
  car: CustomPlanCar;
  index: number;
}

const rupees = (n?: number | null) => `₹${Math.round(Number(n || 0)).toLocaleString("en-IN")}`;

export function RefundCarDialog({ target, onClose, onDone }: { target: RefundTarget | null; onClose: () => void; onDone?: (plan: CustomPlan) => void }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const max = Math.floor(Number(target?.car.refundable_amount || 0));
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!target) return;
    setAmount(max > 0 ? String(max) : "");
    setReason("");
    setError("");
  }, [target, max]);

  const value = amount.trim() === "" ? NaN : Math.round(Number(amount));
  const amountOk = Number.isFinite(value) && value >= 1 && value <= max;
  const reasonOk = reason.trim().length >= 2;
  const plate = target?.car.registration_number || "This car";

  const save = useMutation({
    mutationFn: () =>
      customPlanApi.refundCar(target!.plan.id, target!.car.vehicle_id || target!.index, {
        amount: value === max ? undefined : value,
        reason: reason.trim(),
      }),
    onSuccess: ({ result, message }) => {
      queryClient.invalidateQueries({ queryKey: ["custom-plans"] });
      queryClient.setQueryData(["custom-plan", result.custom_plan.id], result.custom_plan);
      queryClient.invalidateQueries({
        predicate: (q) => typeof q.queryKey[0] === "string" && /^(customer-wallet|customer-360|center-subscription-overview)/.test(q.queryKey[0]),
      });
      const balance = result.wallet?.balance;
      pushToast({
        tone: result.already ? "info" : "success",
        title: result.already ? "Already Refunded" : `Refunded ${rupees(result.refund?.amount)}`,
        message: result.already
          ? message
          : `${plate} — added to the customer's wallet${balance != null ? ` (now ${rupees(balance)})` : ""}.`,
      });
      onDone?.(result.custom_plan);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <Modal open={!!target} onClose={onClose} title="Refund Car" maxWidth="max-w-sm">
      {target && (
        <div className="space-y-4">
          <div className="rounded-xl border border-[var(--color-card-border)] px-3.5 py-3">
            <p className="text-sm font-semibold text-black">
              <span className="font-mono-num">{plate}</span>
              {target.car.vehicle_type_name ? <span className="font-normal text-gray-500"> · {toTitle(target.car.vehicle_type_name)}</span> : null}
            </p>
            <p className="mt-0.5 text-sm text-black">
              {target.car.status === "skipped" ? "Its Share Of The Plan" : "Unused Washes Worth"}{" "}
              <span className="font-mono-num font-bold">{rupees(max)}</span>
            </p>
          </div>
          <Input
            label="Refund Amount (₹)"
            inputMode="numeric"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 7))}
            error={amount.trim() !== "" && !amountOk ? `Between ₹1 and ${rupees(max)}.` : undefined}
            hint={`Up to ${rupees(max)}.`}
          />
          <Input
            label="Reason"
            maxLength={300}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Customer sold the car"
          />
          <p className="rounded-xl bg-[var(--color-primary-light)] px-3 py-2.5 text-sm text-[#0E1A33]">
            {amountOk ? rupees(value) : "The refund"} goes to the customer&apos;s wallet — they can ask you to pay it back. {plate}&apos;s pass ends now.
          </p>
          {error && (
            <p className="text-sm text-[var(--color-error)]" role="alert">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
              Back
            </Button>
            <Button className="min-h-11 flex-1" isLoading={save.isPending} disabled={!amountOk || !reasonOk} onClick={() => save.mutate()}>
              <Undo2 className="h-4 w-4" /> Refund
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
