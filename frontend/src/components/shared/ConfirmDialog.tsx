import { AlertTriangle } from "lucide-react";
import { Button, Modal } from "../ui";
import { useConfirmDialogState } from "../../context/ConfirmContext";

/**
 * Single shared instance (mounted once in App.tsx) rendering whatever the
 * current useConfirm() request is — see ConfirmContext.tsx for why this
 * replaces every window.confirm()/confirm() call in the app.
 */
export function ConfirmDialog() {
  const { request, respond } = useConfirmDialogState();
  const tone = request?.tone ?? "danger";

  return (
    <Modal open={!!request} onClose={() => respond(false)} title={request?.title ?? ""}>
      {request && (
        <div className="space-y-4">
          {request.message && (
            <div className={tone === "danger" ? "flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2.5 text-sm text-[var(--color-text-secondary)]" : "text-sm text-[var(--color-text-secondary)]"}>
              {tone === "danger" && <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-error)]" />}
              <span>{request.message}</span>
            </div>
          )}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={() => respond(false)}>
              {request.cancelLabel ?? "Cancel"}
            </Button>
            <Button variant={tone === "danger" ? "danger" : "primary"} className="flex-1" onClick={() => respond(true)} autoFocus>
              {request.confirmLabel ?? (tone === "danger" ? "Delete" : "Confirm")}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
