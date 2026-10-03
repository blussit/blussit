import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Phone, MessageCircle } from "lucide-react";

// Matches the public contact number displayed in the site footer. A Vite env
// value can replace it for a different production WhatsApp business account.
const DEFAULT_WHATSAPP_NUMBER = "918962288774";
const DEFAULT_CALL_NUMBER = "8962288774";

/** Space kept between the stack and the landing hero's stats panel. */
const PANEL_GAP = 12;

/** Opens the Blussit WhatsApp chat. `text` pre-fills the message (default:
 *  a booking enquiry); a click event passed straight from onClick is ignored. */
export function openBlussitWhatsApp(text?: unknown) {
  const phone = import.meta.env.VITE_WHATSAPP_NUMBER || DEFAULT_WHATSAPP_NUMBER;
  const message = encodeURIComponent(typeof text === "string" && text.trim() ? text : "Hi Blussit, I would like to book a car wash.");
  window.open(`https://wa.me/${phone}?text=${message}`, "_blank", "noopener,noreferrer");
}

function getBlussitCallNumber() {
  return import.meta.env.VITE_CALL_NUMBER || DEFAULT_CALL_NUMBER;
}

export function WhatsAppFloatingButton() {
  // This button is `position: fixed` in the same bottom-right corner the
  // site footer's phone/email/social tiles scroll up into — on a phone,
  // its hit area sat right on top of the footer's "tel:" tile, silently
  // swallowing taps meant for the phone number. Fading it out for as long
  // as the footer is on screen removes the overlap entirely, and it
  // reappears the moment the user scrolls back up.
  const [overFooter, setOverFooter] = useState(false);
  // Phones: the landing's first screen ends in the trust row, right where
  // this stack sits — keep that screen clean (as designed) and fade the
  // buttons in once the visitor starts scrolling.
  const [phoneAtTop, setPhoneAtTop] = useState(() => typeof window !== "undefined" && window.innerWidth < 1024 && window.scrollY < 160);
  const stackRef = useRef<HTMLDivElement>(null);
  // The landing hero's stats panel shares this corner on shorter screens
  // (1024×768, 1366×768, most laptops). While the corner would cover it, the
  // stack sits in the page just above the panel instead, so it scrolls with
  // it rather than the panel sliding underneath; once the panel has scrolled
  // clear of the corner, the stack docks there again.
  const [lift, setLift] = useState<{ top: number; right: number } | null>(null);

  useEffect(() => {
    const update = () => setPhoneAtTop(window.innerWidth < 1024 && window.scrollY < 160);
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  useEffect(() => {
    const footer = document.querySelector("footer");
    if (!footer) return;
    const observer = new IntersectionObserver(([entry]) => setOverFooter(entry.isIntersecting), { rootMargin: "0px 0px -80px 0px" });
    observer.observe(footer);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let frame = 0;
    const place = () => {
      frame = 0;
      const stack = stackRef.current;
      const panel = document.querySelector<HTMLElement>("[data-hero-stats]");
      if (!stack || !panel || !panel.offsetParent) return setLift(null);
      const p = panel.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      const inset = vw >= 640 ? 24 : 16;
      const w = stack.offsetWidth;
      const h = stack.offsetHeight;
      const dockTop = window.innerHeight - inset - h;
      const covers =
        p.right > vw - inset - w - PANEL_GAP &&
        p.left < vw - inset + PANEL_GAP &&
        p.bottom > dockTop - PANEL_GAP &&
        p.top < dockTop + h + PANEL_GAP;
      const liftedTop = p.top - PANEL_GAP - h;
      const headerBottom = document.querySelector("header")?.getBoundingClientRect().bottom ?? 0;
      // Too little room above the panel (tiny windows): stay docked.
      if (!covers || liftedTop < headerBottom + 8) return setLift(null);
      const next = { top: Math.round(liftedTop + window.scrollY), right: Math.round(vw - p.right) };
      setLift((prev) => (prev && prev.top === next.top && prev.right === next.right ? prev : next));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(place);
    };
    place();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // Late layout shifts above the hero (the offer strip, web fonts) move the panel.
    const resize = new ResizeObserver(schedule);
    resize.observe(document.body);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      resize.disconnect();
      cancelAnimationFrame(frame);
    };
  }, []);

  return createPortal(
    <div
      ref={stackRef}
      aria-hidden={overFooter || phoneAtTop}
      style={lift ? { position: "absolute", top: lift.top, right: lift.right, bottom: "auto" } : undefined}
      className={`fixed bottom-4 right-4 z-[99999] flex flex-col items-end gap-2.5 transition-opacity duration-200 sm:bottom-6 sm:right-6 sm:gap-3 ${overFooter || phoneAtTop ? "pointer-events-none opacity-0" : "opacity-100"}`}
    >
      <a
        href={`tel:${getBlussitCallNumber()}`}
        aria-label="Call Blussit to book"
        className="group flex h-[40px] flex-row items-center gap-2 outline-none sm:h-[58px]"
      >
        <span className="hidden rounded-[10px] border border-white/[0.08] bg-[#181818] px-3.5 py-2.5 text-left leading-tight 2xl:block shadow-[0_8px_24px_rgba(0,0,0,0.30)] transition-shadow duration-200 group-hover:shadow-[0_10px_28px_rgba(0,0,0,0.38)] sm:px-3.5 sm:py-2.5">
          <span className="block text-[11px] font-medium text-white/60 sm:text-[12px]">Tap To Call</span>
          <span className="mt-0.5 block whitespace-nowrap text-[14px] font-bold text-white sm:text-[16px]">Call To Book</span>
        </span>

        <span className="animate-call-ring flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-full border-2 border-white/90 bg-[#2563EB] text-white shadow-[0_8px_24px_rgba(0,0,0,0.25)] transition-[transform,box-shadow] duration-200 sm:h-[58px] sm:w-[58px] group-hover:scale-[1.08] group-hover:shadow-[0_10px_28px_rgba(0,0,0,0.32)]">
          <Phone className="h-[16px] w-[16px] sm:h-[24px] sm:w-[24px] text-white" strokeWidth={2.5} />
        </span>
      </a>

      <button
        type="button"
        aria-label="Chat with Blussit on WhatsApp"
        onClick={() => openBlussitWhatsApp()}
        className="group flex h-[40px] flex-row items-center gap-2 outline-none sm:h-[58px]"
      >
        <span className="hidden rounded-[10px] border border-white/[0.08] bg-[#181818] px-3.5 py-2.5 text-left leading-tight 2xl:block shadow-[0_8px_24px_rgba(0,0,0,0.30)] transition-shadow duration-200 group-hover:shadow-[0_10px_28px_rgba(0,0,0,0.38)] sm:px-3.5 sm:py-2.5">
          <span className="block text-[11px] font-medium text-white/60 sm:text-[12px]">Need Help?</span>
          <span className="mt-0.5 block whitespace-nowrap text-[14px] font-bold text-white sm:text-[16px]">Chat With Us</span>
        </span>

        <span className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-full border-2 border-white/90 bg-[#25D366] text-white shadow-[0_8px_24px_rgba(0,0,0,0.25)] transition-[transform,box-shadow] duration-200 group-hover:scale-[1.08] group-hover:shadow-[0_10px_28px_rgba(0,0,0,0.32)] sm:h-[58px] sm:w-[58px]">
          <MessageCircle className="h-[22px] w-[22px] sm:h-[32px] sm:w-[32px] text-white" strokeWidth={2.5} />
        </span>
      </button>
    </div>,
    document.body,
  );
}
