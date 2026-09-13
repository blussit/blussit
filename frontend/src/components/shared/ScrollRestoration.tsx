import { useEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

const KEY_PREFIX = "blussit:scrollY:";

/**
 * "Stay where I was" for the CURRENT browser tab only — hitting Back
 * returns to the same scroll position on the page you came from, instead
 * of snapping to the top; a fresh forward navigation still starts at the
 * top, same as every site behaves today. Deliberately sessionStorage
 * (cleared the moment the tab/window closes), never localStorage — the
 * ask was to remember state for the current visit, not for days.
 */
export function ScrollRestoration() {
  const location = useLocation();
  const navType = useNavigationType(); // "POP" | "PUSH" | "REPLACE"
  const keyRef = useRef(KEY_PREFIX + location.pathname + location.search);

  // Runs on every route change: restore the outgoing... no, the INCOMING
  // page's saved position on Back/Forward, or reset to top on a normal
  // forward navigation.
  useEffect(() => {
    keyRef.current = KEY_PREFIX + location.pathname + location.search;
    if (navType === "POP") {
      try {
        const saved = sessionStorage.getItem(keyRef.current);
        if (saved != null) {
          // One frame so the new page's content has laid out first —
          // scrolling before that clamps to whatever's rendered so far.
          requestAnimationFrame(() => window.scrollTo(0, Number(saved)));
          return;
        }
      } catch {
        // Private browsing / storage blocked — nothing to restore.
      }
    }
    window.scrollTo(0, 0);
  }, [location.key, navType]);

  // A single persistent scroll listener keeps saving the CURRENT route's
  // position as the user scrolls — far simpler and more reliable than
  // trying to capture it at the moment of navigating away.
  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        try {
          sessionStorage.setItem(keyRef.current, String(window.scrollY));
        } catch {
          // ignore
        }
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return null;
}
