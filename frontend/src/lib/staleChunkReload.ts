/**
 * A tab opened before a deploy still points at the old hashed chunks, which
 * the new deploy no longer serves, so its next lazy route fails to load.
 * Reload once — but only when the live index.html no longer references this
 * page's entry script: a transient network failure (e.g. the idle booking
 * prefetch on a flaky connection) must not reload the page under the customer.
 */
const RELOADED_AT_KEY = "blussit:stale-chunk-reload-at";
const LOOP_GUARD_MS = 60_000;

function reloadedRecently(): boolean {
  try {
    const at = Number(sessionStorage.getItem(RELOADED_AT_KEY));
    return Date.now() - at < LOOP_GUARD_MS;
  } catch {
    // No storage means no loop guard, so never auto-reload.
    return true;
  }
}

async function deployChanged(): Promise<boolean> {
  const entry = document.querySelector<HTMLScriptElement>('script[type="module"][src]')?.getAttribute("src");
  if (!entry) return false;
  try {
    const res = await fetch("/", { cache: "no-store" });
    return res.ok && !(await res.text()).includes(entry);
  } catch {
    return false;
  }
}

export function reloadOnStaleChunks(): void {
  let checking = false;
  window.addEventListener("vite:preloadError", () => {
    if (checking || reloadedRecently()) return;
    checking = true;
    void deployChanged().then((changed) => {
      checking = false;
      if (!changed) return;
      try {
        sessionStorage.setItem(RELOADED_AT_KEY, String(Date.now()));
      } catch {
        return;
      }
      window.location.reload();
    });
  });
}
