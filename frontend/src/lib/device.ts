/**
 * A random id for THIS browser, made once and kept in localStorage, sent as
 * `X-Device-Id` on every API call. The server uses it as one key part of its
 * login / OTP throttles (always together with the IP — it is never trusted on
 * its own) and remembers (hashed) the ones that signed in successfully, so a
 * stranger's wrong passwords can't lock the real owner out.
 *
 * Storage can be blocked (private windows, strict settings): then the id lives
 * for this page load only.
 */
const KEY = "blussit:device";
let memory: string | null = null;

function randomId(): string {
  try {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    // No Web Crypto: still unguessable enough for a throttle key.
    return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
  }
}

export function getDeviceId(): string {
  if (memory) return memory;
  try {
    const stored = localStorage.getItem(KEY);
    if (stored && /^[A-Za-z0-9_-]{16,64}$/.test(stored)) {
      memory = stored;
      return stored;
    }
  } catch {
    // storage blocked — fall through to an in-memory id
  }
  memory = randomId();
  try {
    localStorage.setItem(KEY, memory);
  } catch {
    // storage blocked — the id lasts for this page load
  }
  return memory;
}
