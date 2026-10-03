/**
 * Captain app v2 primitives — white ground, navy type (#0E1A33), one blue
 * primary (#0A66F0), muted #5F6878, tints #E8F0FE / #EEF3FA, hairline
 * borders #E4E9F1, 14–18 px radii and ≥48 px tap targets. Phones first.
 */
import { type ButtonHTMLAttributes, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronLeft, Loader2, X } from "lucide-react";
import { cn } from "../../lib/cn";

type BtnVariant = "primary" | "secondary" | "outline" | "danger" | "success";

const BTN: Record<BtnVariant, string> = {
  primary: "bg-[#0A66F0] text-white active:bg-[#0852C2] disabled:bg-[#9DBDF2]",
  secondary: "bg-[#E8F0FE] text-[#0A66F0] active:bg-[#D6E4FD] disabled:text-[#8FB2EE]",
  outline: "border border-[#E4E9F1] bg-white text-[#0E1A33] active:bg-[#EEF3FA] disabled:text-[#A3AAB6]",
  danger: "bg-[#DC2626] text-white active:bg-[#B91C1C] disabled:bg-[#F2A7A7]",
  success: "bg-[#16A34A] text-white active:bg-[#15803D]",
};

export function Btn({
  variant = "primary",
  loading,
  className,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; loading?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      className={cn(
        "inline-flex min-h-[52px] items-center justify-center gap-2 rounded-2xl px-4 text-[15px] font-bold transition-colors disabled:cursor-not-allowed",
        BTN[variant],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

/** Sticky screen header: back arrow, centred title, optional right slot. */
export function TopBar({ title, back, right }: { title: string; back?: string | (() => void); right?: ReactNode }) {
  const navigate = useNavigate();
  return (
    <header className="sticky top-0 z-20 -mx-4 mb-3 flex h-14 items-center justify-between gap-2 border-b border-[#E4E9F1] bg-white/95 px-2 backdrop-blur">
      {back ? (
        <button
          type="button"
          aria-label="Back"
          onClick={() => (typeof back === "string" ? navigate(back) : back())}
          className="flex h-11 w-11 items-center justify-center rounded-full text-[#0E1A33] active:bg-[#EEF3FA]"
        >
          <ChevronLeft className="h-6 w-6" />
        </button>
      ) : (
        <span className="w-11" />
      )}
      <h1 className="min-w-0 flex-1 truncate text-center text-[17px] font-bold text-[#0E1A33]">{title}</h1>
      <div className="flex h-11 min-w-11 items-center justify-end">{right}</div>
    </header>
  );
}

/** Buttons pinned to the bottom of a job screen, inside the phone column. */
export function BottomBar({ children }: { children: ReactNode }) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-30 mx-auto w-full max-w-[480px] border-t border-[#E4E9F1] bg-white px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3">
      {children}
    </div>
  );
}

export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 bg-[#0E1A33]/40"
            onClick={onClose}
          />
          <motion.div
            initial={{ y: 40, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 40, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="absolute inset-x-0 bottom-0 mx-auto max-h-[88vh] w-full max-w-[480px] overflow-y-auto rounded-t-[22px] bg-white px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3"
          >
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-[#E4E9F1]" />
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-lg font-bold text-[#0E1A33]">{title}</h2>
              <button type="button" aria-label="Close" onClick={onClose} className="flex h-10 w-10 items-center justify-center rounded-full text-[#5F6878] active:bg-[#EEF3FA]">
                <X className="h-5 w-5" />
              </button>
            </div>
            {children}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("rounded-[18px] border border-[#E4E9F1] bg-white", className)}>{children}</div>;
}

type Tone = "blue" | "green" | "amber" | "gray" | "red";
const PILL: Record<Tone, string> = {
  blue: "bg-[#E8F0FE] text-[#0A66F0]",
  green: "bg-[#E7F6EC] text-[#15803D]",
  amber: "bg-[#FFF5DB] text-[#9A6400]",
  gray: "bg-[#EEF3FA] text-[#5F6878]",
  red: "bg-[#FDECEC] text-[#B91C1C]",
};

export function Pill({ tone = "gray", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold", PILL[tone], className)}>{children}</span>;
}

export function Notice({ tone = "blue", icon, children }: { tone?: Tone; icon?: ReactNode; children: ReactNode }) {
  return (
    <div className={cn("flex items-start gap-2.5 rounded-[14px] px-3.5 py-3 text-sm font-medium", PILL[tone])}>
      {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function StatTile({ label, value, tone = "navy" }: { label: string; value: ReactNode; tone?: "navy" | "green" | "blue" }) {
  const color = { navy: "text-[#0E1A33]", green: "text-[#15803D]", blue: "text-[#0A66F0]" }[tone];
  return (
    <div className="rounded-[16px] border border-[#E4E9F1] bg-white px-3 py-3">
      <p className={cn("tabular-nums text-[26px] font-extrabold leading-none", color)}>{value}</p>
      <p className="mt-1.5 text-xs font-semibold text-[#5F6878]">{label}</p>
    </div>
  );
}

/** One labelled line on a details card: icon tile, label, value. */
export function InfoRow({ icon, label, children, right }: { icon: ReactNode; label: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0A66F0]">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold text-[#5F6878]">{label}</p>
        <div className="mt-0.5 text-[15px] font-semibold text-[#0E1A33]">{children}</div>
      </div>
      {right && <div className="shrink-0 self-center">{right}</div>}
    </div>
  );
}

export function PageTitle({ children, sub }: { children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="pb-1 pt-5">
      {sub && <p className="text-[13px] font-semibold text-[#5F6878]">{sub}</p>}
      <h1 className="text-[24px] font-extrabold leading-tight text-[#0E1A33]">{children}</h1>
    </div>
  );
}
