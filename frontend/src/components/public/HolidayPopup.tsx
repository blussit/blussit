import { useId } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import type { PublicHoliday } from "../../api/holidays";
import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";
import { toTitle } from "../../lib/titleCase";
import { useDialogStack } from "../ui/Modal";

/**
 * The friendly "we're on a break" card: a big smile, the admin's own
 * message, a thank-you, the days we can't serve and when booking reopens,
 * with a button straight to the booking page on that day. Loaded lazily
 * (only when a holiday is actually announced) and rendered on <body> like
 * every other popup.
 */
export default function HolidayPopup({
  holiday,
  open,
  onClose,
  bookPath,
}: {
  holiday: PublicHoliday;
  open: boolean;
  onClose: () => void;
  /** "/book" or "/app/book" — the date is added as ?date=. */
  bookPath: string;
}) {
  const navigate = useNavigate();
  const titleId = useId();
  useDialogStack(open, onClose, titleId);
  useBodyScrollLock(open);
  if (typeof document === "undefined") return null;

  const book = () => {
    onClose();
    navigate(`${bookPath}?date=${encodeURIComponent(holiday.resume_date)}`);
  };

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-[100000] flex items-center justify-center overflow-y-auto overscroll-contain p-4" role="dialog" aria-modal="true" aria-labelledby={titleId}>
          <motion.div
            className="fixed inset-0 bg-[#0E1A33]/55 backdrop-blur-[4px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.div
            initial={{ opacity: 0, y: 22, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.97 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className="relative z-10 m-auto w-full max-w-[420px] overflow-hidden rounded-[22px] border border-[#F3E5B5] bg-white px-6 pb-6 pt-8 text-center shadow-[0_30px_90px_rgba(14,26,51,0.30)] sm:px-8"
          >
            <HolidayCard holiday={holiday} titleId={titleId} onBook={book} onClose={onClose} />
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

/** The card's content — shared by the popup and the admin's live preview. */
export function HolidayCard({
  holiday,
  titleId,
  onBook,
  onClose,
}: {
  holiday: Pick<PublicHoliday, "title" | "message" | "dates_label" | "resume_label" | "resume_day">;
  titleId?: string;
  onBook?: () => void;
  onClose?: () => void;
}) {
  return (
    <>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="absolute right-3.5 top-3.5 flex h-9 w-9 items-center justify-center rounded-full border border-[#E4E9F1] bg-white text-[#0E1A33] transition-colors hover:bg-[#F6F8FC]"
      >
        <X className="h-[18px] w-[18px]" strokeWidth={2.4} />
      </button>

      <div className="mx-auto flex h-[104px] w-[104px] items-center justify-center rounded-full bg-[#FFF6D6]" aria-hidden>
        <span className="text-[68px] leading-none">😊</span>
      </div>

      <h2 id={titleId} className="mt-5 font-display text-[24px] font-extrabold leading-[1.15] tracking-[-0.01em] text-[#0E1A33]">
        {holiday.title ? toTitle(holiday.title) : "We're On A Short Break"}
      </h2>

      {holiday.message && <p className="mt-3 whitespace-pre-line text-[15px] leading-[1.5] text-[#3A4456]">{holiday.message}</p>}

      <p className="mt-4 text-[15px] font-bold text-[#0E1A33]">Thank You For Choosing Blussit!</p>

      <div className="mt-4 space-y-1.5 rounded-[14px] bg-[#FAFAFA] px-4 py-3 text-[14px] leading-[1.45] text-[#3A4456]">
        <p>{toTitle(`We're not able to serve on ${holiday.dates_label}.`)}</p>
        <p className="font-semibold text-[#0E1A33]">{toTitle(`You can book from ${holiday.resume_label}.`)}</p>
      </div>

      <button
        type="button"
        onClick={onBook}
        className="mt-5 flex h-[50px] w-full items-center justify-center gap-2 rounded-[14px] bg-[#FFD21F] px-4 font-display text-[16px] font-bold text-[#0E1A33] shadow-[0_8px_20px_-10px_rgba(232,169,0,0.7)] transition hover:bg-[#F5C400] active:scale-[0.99]"
      >
        Book For {holiday.resume_day}
        <ArrowRight className="h-[18px] w-[18px]" strokeWidth={2.4} />
      </button>
      <button type="button" onClick={onClose} className="mt-3 w-full text-center text-[13.5px] font-medium text-[#5F6878] hover:text-[#0E1A33]">
        Close
      </button>
    </>
  );
}
