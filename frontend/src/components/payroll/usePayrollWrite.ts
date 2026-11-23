import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";

/**
 * Shared plumbing for every payroll write: after it lands, every `payroll-*`
 * read refreshes (the live-refresh funnel does too, a beat later), the
 * success toast carries the SERVER's own message, and a failure toast shows
 * the server's reason.
 */
export function usePayrollWrite() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const refresh = () =>
    queryClient.invalidateQueries({ predicate: (q) => typeof q.queryKey[0] === "string" && q.queryKey[0].startsWith("payroll-") });
  return {
    refresh,
    ok: (title: string, message?: string) => {
      void refresh();
      push({ tone: "success", title, message: message || undefined });
    },
    fail: (err: unknown, title = "That Didn't Save") => push({ tone: "error", title, message: getErrorMessage(err) }),
  };
}
