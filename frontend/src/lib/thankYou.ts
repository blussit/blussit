/**
 * The /thank-you page's ticket travels OUT of the URL: a `?token=…` in the
 * address bar was sent to Google Analytics and the Meta Pixel as part of
 * the page URL (both record every SPA navigation), and that token opens the
 * booking and its service code. The flows hand it over in router state and
 * keep a copy in this tab's sessionStorage, so a refresh still shows the
 * page; an old-style link with ?token= is moved here by an inline script in
 * index.html before any analytics run.
 */
export const THANK_YOU_TOKEN_KEY = "blussit:thank-you";

export function stashThankYouToken(token: string) {
  try {
    sessionStorage.setItem(THANK_YOU_TOKEN_KEY, token);
  } catch {
    // storage blocked — router state still carries it for this visit
  }
}

export function readStashedThankYouToken(): string | null {
  try {
    return sessionStorage.getItem(THANK_YOU_TOKEN_KEY);
  } catch {
    return null;
  }
}
