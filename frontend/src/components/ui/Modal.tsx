import { type ReactNode, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";

/** Ids of the currently open modals, innermost last. */
const openStack: string[] = [];

/**
 * Escape closes only the TOP-most open dialog (keyboard users had no way out
 * but tabbing to the X) — one opened from another closes first, its parent
 * stays. Every full-screen overlay registers here, not just <Modal>.
 */
export function useDialogStack(open: boolean, onClose: () => void, id: string) {
  // Callers pass an inline onClose; a ref keeps the effect below from
  // re-registering (and re-ordering the stack) on every render.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    openStack.push(id);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && openStack[openStack.length - 1] === id) {
        e.preventDefault();
        closeRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      const i = openStack.lastIndexOf(id);
      if (i >= 0) openStack.splice(i, 1);
    };
  }, [open, id]);
}

export function Modal({
  open,
  onClose,
  title,
  children,
  maxWidth = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  maxWidth?: string;
}) {
  const titleId = useId();
  useDialogStack(open, onClose, titleId);
  // The page behind stays still while the dialog is open.
  useBodyScrollLock(open);
  // Rendered on <body>: inside an animated/transformed card a `fixed` layer is
  // trapped in that card, so the dimmer covered only part of the page.
  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby={titleId}>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-gray-900/40 backdrop-blur-[2px]"
            onClick={onClose}
          />
          <motion.div
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={{ duration: 0.18 }}
            className={`relative z-10 w-full ${maxWidth} max-h-[85vh] overflow-y-auto rounded-2xl border border-[var(--color-card-border)] bg-white p-5 sm:p-6 shadow-[0_24px_60px_-16px_rgba(0,0,0,0.28)]`}
          >
            <div className="mb-4 flex items-center justify-between gap-3">
              <h3 id={titleId} className="min-w-0 text-lg font-semibold text-[var(--ui-ink,#000)]">{title}</h3>
              <button
                onClick={onClose}
                aria-label="Close"
                className="shrink-0 rounded-full p-1.5 text-gray-400 transition-colors hover:bg-[var(--ui-tint,#FFF4CD)] hover:text-[var(--ui-ink,#000)]"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            {children}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
