import { useEffect } from "react";

// Shared by every open overlay, so two at once release only when both close.
let locks = 0;
let saved: { y: number; bodyStyle: string } | null = null;

/**
 * Freezes the page behind a popup while `active`. `overflow: hidden` alone
 * doesn't stop iOS Safari, so the body is pinned in place at the current
 * scroll position and put back exactly there when the popup closes.
 */
export function useBodyScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const { body, documentElement: html } = document;
    if (locks++ === 0) {
      const y = window.scrollY;
      const scrollbar = window.innerWidth - html.clientWidth; // keep the layout from jumping on desktop
      saved = { y, bodyStyle: body.getAttribute("style") ?? "" };
      Object.assign(body.style, { position: "fixed", top: `-${y}px`, left: "0", right: "0", width: "100%", overflow: "hidden" });
      if (scrollbar > 0) body.style.paddingRight = `${scrollbar}px`;
    }
    return () => {
      if (--locks > 0 || !saved) return;
      const { y, bodyStyle } = saved;
      saved = null;
      if (bodyStyle) body.setAttribute("style", bodyStyle);
      else body.removeAttribute("style");
      // index.css makes html scroll smoothly — jumping back must be instant.
      const behavior = html.style.scrollBehavior;
      html.style.scrollBehavior = "auto";
      window.scrollTo(0, y);
      html.style.scrollBehavior = behavior;
    };
  }, [active]);
}
