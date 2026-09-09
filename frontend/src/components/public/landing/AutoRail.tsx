import { useEffect, useRef, type ReactNode } from "react";

/**
 * Phones: one horizontal, snap-scrolling row that auto-advances every few
 * seconds. It pauses while the row is touched, after any manual scroll,
 * while it is off-screen, and under prefers-reduced-motion. From `sm` up
 * it is a plain grid — pass the columns/gaps as `gridClassName` with
 * `sm:`/`md:`/`lg:` prefixes.
 *
 * Children size themselves for both modes, e.g.
 * `w-[80vw] max-w-[340px] shrink-0 snap-center sm:w-auto sm:max-w-none`.
 */
export function AutoRail({
  children,
  gridClassName = "",
  className = "",
  intervalMs = 1800,
}: {
  children: ReactNode;
  gridClassName?: string;
  className?: string;
  intervalMs?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const phone = window.matchMedia("(max-width: 639px)");
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let touching = false;
    let visible = false;
    let holdUntil = 0; // the user interacted — wait a full interval before moving again
    let autoUntil = 0; // our own smooth scroll is in flight — its scroll events aren't the user's

    const items = () => Array.from(el.children) as HTMLElement[];
    const centerOf = (c: HTMLElement) => {
      const r = c.getBoundingClientRect();
      return r.left - el.getBoundingClientRect().left + el.scrollLeft + r.width / 2;
    };

    const step = () => {
      if (!phone.matches || reduced.matches || touching || !visible || Date.now() < holdUntil) return;
      const list = items();
      if (list.length < 2) return;
      const mid = el.scrollLeft + el.clientWidth / 2;
      let current = 0;
      let best = Number.POSITIVE_INFINITY;
      list.forEach((c, i) => {
        const d = Math.abs(centerOf(c) - mid);
        if (d < best) {
          best = d;
          current = i;
        }
      });
      const next = list[(current + 1) % list.length];
      autoUntil = Date.now() + Math.min(1500, intervalMs - 500);
      el.scrollTo({ left: centerOf(next) - el.clientWidth / 2, behavior: "smooth" });
    };

    const onTouchStart = () => {
      touching = true;
    };
    const onTouchEnd = () => {
      touching = false;
      holdUntil = Date.now() + intervalMs;
    };
    const onScroll = () => {
      if (Date.now() > autoUntil) holdUntil = Date.now() + intervalMs;
    };

    const timer = window.setInterval(step, intervalMs);
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchend", onTouchEnd);
    el.addEventListener("touchcancel", onTouchEnd);
    el.addEventListener("scroll", onScroll, { passive: true });
    const io = new IntersectionObserver(
      ([entry]) => {
        visible = entry.isIntersecting;
      },
      { threshold: 0.4 },
    );
    io.observe(el);

    return () => {
      window.clearInterval(timer);
      io.disconnect();
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
      el.removeEventListener("scroll", onScroll);
    };
  }, [intervalMs]);

  return (
    <div
      ref={ref}
      data-auto-rail=""
      className={`-mx-4 flex snap-x snap-mandatory gap-4 overflow-x-auto px-4 pb-2 hide-scrollbar sm:mx-0 sm:grid sm:overflow-visible sm:px-0 sm:pb-0 ${gridClassName} ${className}`}
    >
      {children}
    </div>
  );
}
