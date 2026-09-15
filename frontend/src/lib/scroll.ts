/** Jumps straight to the top of the page — used after a booking wizard's
 *  "Add This Vehicle"/"Edit" actions clear or refill the fields above.
 *  A plain `window.scrollTo({ top: 0, behavior: "smooth" })` here had two
 *  problems: (1) if a field was still focused, animating the scroll while
 *  the mobile keyboard closes made Safari/Chrome zoom the whole page in,
 *  and (2) firing it before the cleared/added sections above had settled
 *  to their final height let the browser's scroll-anchoring correction
 *  fight the in-flight smooth scroll, sometimes leaving the page stuck
 *  near the button instead of at the top. Blurring first and waiting a
 *  frame for layout to settle, then jumping instantly, avoids both. */
export function scrollToTopNow() {
  (document.activeElement as HTMLElement | null)?.blur();
  requestAnimationFrame(() => {
    requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: "auto" }));
  });
}
