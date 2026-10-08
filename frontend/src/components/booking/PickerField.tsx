import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown, X } from "lucide-react";
import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";

function useIsPhone(): boolean {
  const query = "(max-width: 639px)";
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setPhone(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return phone;
}

/**
 * One of the three pickers from the booking mockup (Car Type · Service ·
 * Date & Time): an icon, a small label and the current choice. Tapping it
 * opens its options — a dropdown panel on a laptop, a bottom sheet on a
 * phone (thumb reach, full width). `children` gets a `close` callback.
 */
export function PickerField({
  icon,
  label,
  value,
  placeholder,
  open,
  onOpenChange,
  sheetTitle,
  invalid = false,
  onClick,
  children,
}: {
  icon: ReactNode;
  label: string;
  value?: ReactNode;
  placeholder: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sheetTitle?: string;
  invalid?: boolean;
  /** Replaces the dropdown (e.g. Date & Time jumps to the slots). */
  onClick?: () => void;
  children?: (close: () => void) => ReactNode;
}) {
  const phone = useIsPhone();
  const wrap = useRef<HTMLDivElement>(null);
  const close = () => onOpenChange(false);
  const showPanel = open && !!children;
  useBodyScrollLock(showPanel && phone);

  useEffect(() => {
    if (!showPanel) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    const onDown = (e: MouseEvent) => {
      if (!phone && wrap.current && !wrap.current.contains(e.target as Node)) close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showPanel, phone]);

  return (
    <div ref={wrap} className={`relative min-w-0 ${showPanel && !phone ? "z-30" : ""}`}>
      <button
        type="button"
        aria-haspopup={children ? "listbox" : undefined}
        aria-expanded={children ? showPanel : undefined}
        onClick={() => (onClick ? onClick() : onOpenChange(!open))}
        className={`flex min-h-[64px] w-full items-center gap-2.5 rounded-[14px] border bg-white px-3 py-2 text-left transition hover:border-[#0A66F0]/50 ${
          showPanel ? "border-[#0A66F0] ring-2 ring-[#0A66F0]/12" : invalid ? "border-[#F2B8B5]" : "border-[#E4E9F1]"
        }`}
      >
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[12px] bg-[#EEF3FA] text-[#0A66F0]">{icon}</span>
        <span className="min-w-0 flex-1">
          <span className="block text-[12px] font-medium text-[#5F6878]">{label}</span>
          <span className={`line-clamp-2 break-words text-[14px] font-semibold leading-snug ${value ? "text-[#0E1A33]" : "text-[#9AA3B2]"}`}>{value || placeholder}</span>
        </span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-[#0E1A33] transition-transform ${showPanel ? "rotate-180" : ""}`} />
      </button>

      {/* Laptop: a panel under the field */}
      <AnimatePresence>
        {showPanel && !phone && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.14 }}
            role="listbox"
            aria-label={sheetTitle || label}
            className="absolute left-0 top-full mt-2 max-h-[min(440px,70vh)] w-[max(100%,340px)] overflow-y-auto rounded-[16px] border border-[#E4E9F1] bg-white p-2 shadow-[0_24px_48px_-16px_rgba(14,26,51,0.28)]"
          >
            {children?.(close)}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Phone: a bottom sheet */}
      {typeof document !== "undefined" &&
        createPortal(
          <AnimatePresence>
            {showPanel && phone && (
              <div className="fixed inset-0 z-[60] flex items-end">
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-[#0E1A33]/40" onClick={close} />
                <motion.div
                  initial={{ y: "100%" }}
                  animate={{ y: 0 }}
                  exit={{ y: "100%" }}
                  transition={{ type: "tween", duration: 0.22, ease: "easeOut" }}
                  role="listbox"
                  aria-label={sheetTitle || label}
                  className="relative z-10 max-h-[82dvh] w-full overflow-y-auto rounded-t-[22px] bg-white px-3 pb-[max(16px,env(safe-area-inset-bottom))] pt-2"
                >
                  <div className="sticky top-0 z-10 -mx-3 bg-white px-4 pb-2 pt-1">
                    <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-[#D5DBE5]" />
                    <div className="flex items-center justify-between">
                      <p className="font-display text-[17px] font-bold text-[#0E1A33]">{sheetTitle || label}</p>
                      <button type="button" onClick={close} aria-label="Close" className="flex h-8 w-8 items-center justify-center rounded-full bg-[#F1F4F9] text-[#0E1A33]">
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                  {children?.(close)}
                </motion.div>
              </div>
            )}
          </AnimatePresence>,
          document.body
        )}
    </div>
  );
}

/** One row inside a picker panel. */
export function PickerOption({
  selected,
  onSelect,
  children,
  disabled = false,
}: {
  selected: boolean;
  onSelect: () => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      disabled={disabled}
      onClick={onSelect}
      className={`flex w-full items-center gap-3 rounded-[12px] border px-3 py-2.5 text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${
        selected ? "border-[#0A66F0] bg-[#F3F7FF]" : "border-transparent hover:bg-[#F6F8FC]"
      }`}
    >
      {children}
    </button>
  );
}
