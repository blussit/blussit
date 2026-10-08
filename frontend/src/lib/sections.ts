/**
 * The public site is one landing page; the navbar and footer jump to its
 * sections instead of opening separate pages. Sections carry a
 * scroll-margin (index.css) so the sticky header never covers their title.
 */
export type LandingSection = "top" | "services" | "plans" | "reviews" | "how-it-works";

/** The order sections appear in, for working out which one is on screen. */
export const SPY_SECTIONS: LandingSection[] = ["services", "how-it-works", "plans", "reviews"];

export const sectionHref = (section: LandingSection) => (section === "top" ? "/" : `/#${section}`);

/** Scrolls to a section on the current page; false when it isn't rendered yet. */
export function scrollToSection(section: LandingSection | string, behavior: ScrollBehavior = "smooth"): boolean {
  if (section === "top") {
    window.scrollTo({ top: 0, behavior });
    return true;
  }
  const el = document.getElementById(section);
  if (!el) return false;
  el.scrollIntoView({ behavior, block: "start" });
  return true;
}

/** The section currently under the header (for the navbar underline). */
export function sectionInView(offset = 140): LandingSection {
  let current: LandingSection = "top";
  for (const id of SPY_SECTIONS) {
    const el = document.getElementById(id);
    if (el && el.getBoundingClientRect().top <= offset) current = id;
  }
  return current;
}
