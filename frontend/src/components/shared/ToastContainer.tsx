import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useToast, type Toast, type ToastTone } from "../../context/ToastContext";

const toneStyles: Record<ToastTone, { bg: string; icon: typeof Info }> = {
  info: { bg: "border-l-[var(--color-secondary)]", icon: Info },
  success: { bg: "border-l-[var(--color-success)]", icon: CheckCircle2 },
  warning: { bg: "border-l-amber-500", icon: AlertTriangle },
  error: { bg: "border-l-[var(--color-error)]", icon: XCircle },
};

function ToastCard({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const { bg, icon: Icon } = toneStyles[toast.tone];
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 40, transition: { duration: 0.15 } }}
      transition={{ duration: 0.2 }}
      className={`pointer-events-auto flex w-80 items-start gap-3 rounded-xl border-l-4 bg-white p-4 shadow-[var(--shadow-lifted)] ${bg} ${
        toast.onClick ? "cursor-pointer" : ""
      }`}
      onClick={toast.onClick}
    >
      <Icon className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-text-secondary)]" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-[var(--color-text-primary)]">{toast.title}</p>
        {toast.message && <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{toast.message}</p>}
      </div>
      <button
        aria-label="Dismiss"
        onClick={(e) => {
          e.stopPropagation();
          onDismiss();
        }}
        className="rounded-full p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
      >
        <X className="h-4 w-4" />
      </button>
    </motion.div>
  );
}

export function ToastContainer() {
  const { toasts, dismiss } = useToast();
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex flex-col gap-2">
      <AnimatePresence>
        {toasts.map((t) => (
          <ToastCard key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </AnimatePresence>
    </div>
  );
}
