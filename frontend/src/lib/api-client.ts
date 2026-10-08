import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

/**
 * Where the API lives — always VITE_API_BASE_URL. There is deliberately no
 * silent fallback to the live API for production builds: a Vercel preview
 * (or any build) without the variable used to talk to api.blussit.com with
 * real customers' data. vite.config.ts refuses a production build that
 * doesn't set it; the localhost default only serves `vite` dev.
 */
const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL;
if (!configuredBaseUrl && import.meta.env.PROD) {
  console.error("VITE_API_BASE_URL is not set for this build — API calls will fail.");
}
export const API_BASE_URL = configuredBaseUrl || (import.meta.env.PROD ? "/api/v1" : "http://localhost:8000/api/v1");

export const TOKEN_KEY = "dvc_access_token";
export const REFRESH_KEY = "dvc_refresh_token";

/** Default for every API call: a stalled mobile connection fails with a
 *  "try again" instead of a spinner that never ends. Uploads pass their own. */
export const REQUEST_TIMEOUT_MS = 30_000;
export const UPLOAD_TIMEOUT_MS = 120_000;

export const tokenStorage = {
  getAccess: () => {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  getRefresh: () => {
    try {
      return localStorage.getItem(REFRESH_KEY);
    } catch {
      return null;
    }
  },
  set: (access: string, refresh: string) => {
    localStorage.setItem(TOKEN_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear: () => {
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(REFRESH_KEY);
    } catch {
      // storage blocked — nothing stored to clear
    }
    // An unfinished booking (pin, phone, notes) belongs to whoever was
    // signed in — never hand it to the next person on this device.
    try {
      for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
        const key = sessionStorage.key(i);
        if (key?.startsWith("blussit:quickbook:")) sessionStorage.removeItem(key);
      }
    } catch {
      // storage blocked — nothing to clear
    }
  },
};

/** The claims of a JWT (unverified — only for timing/identity hints). */
export function decodeTokenPayload(token: string): Record<string, unknown> | null {
  try {
    return JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    return null;
  }
}

/** True when the token's `exp` is less than `seconds` away (by this device's clock). */
export function tokenExpiresWithin(token: string, seconds: number): boolean {
  const exp = decodeTokenPayload(token)?.exp;
  return typeof exp === "number" && exp * 1000 - Date.now() < seconds * 1000;
}

/** Who a token belongs to (its `sub`), or null. */
export function tokenSubject(token: string | null): string | null {
  if (!token) return null;
  const sub = decodeTokenPayload(token)?.sub;
  return typeof sub === "string" ? sub : null;
}

/**
 * The server said "this session is over" — a 4xx answer that isn't a rate
 * limit or a timeout. Anything else (no answer at all, a timeout, a 5xx, a
 * 429) is the network or the server having a bad moment, and must never
 * sign anyone out: a captain on patchy mobile data used to be thrown to
 * the login page by a single blip during a token refresh.
 */
export function isSessionRejection(error: unknown): boolean {
  if (!axios.isAxiosError(error) || !error.response) return false;
  const status = error.response.status;
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

// ---- session end ------------------------------------------------------------
// The API layer only clears the tokens and announces it; AuthContext drops
// the user, and ProtectedRoute alone sends the visitor to /login (keeping
// where they were). A public page — the homepage, /book, a service page —
// just carries on as a guest instead of bouncing to /login.
type SessionEndListener = (notice: string | null) => void;
const sessionEndListeners = new Set<SessionEndListener>();
export function onSessionEnd(listener: SessionEndListener): () => void {
  sessionEndListeners.add(listener);
  return () => {
    sessionEndListeners.delete(listener);
  };
}
export function endSession(notice: string | null = null) {
  const hadSession = !!(tokenStorage.getAccess() || tokenStorage.getRefresh());
  tokenStorage.clear();
  if (!hadSession) return;
  sessionEndListeners.forEach((listener) => {
    try {
      listener(notice);
    } catch {
      // a listener must never break the request that ended the session
    }
  });
}

function serverMessage(error: unknown): string | null {
  if (!axios.isAxiosError(error)) return null;
  const data = error.response?.data as { message?: unknown } | undefined;
  return typeof data?.message === "string" && data.message ? data.message : null;
}

// ---- token refresh (one at a time, shared with lib/socket.ts) ----------------
let refreshInFlight: Promise<string> | null = null;
// The access token the last refresh handed us: never refresh it again on
// the local clock's say-so — a phone whose clock runs fast would otherwise
// refresh before every single request.
let lastRefreshedAccess: string | null = null;

/** Trade the refresh token for a fresh pair. Concurrent callers share one
 *  request. Rejects on failure; when the SERVER refused the refresh token
 *  (session over, account suspended) the session is ended first. */
export function refreshSession(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;
  const refreshToken = tokenStorage.getRefresh();
  if (!refreshToken) return Promise.reject(new Error("No session to refresh"));
  refreshInFlight = (async () => {
    try {
      const { data } = await axios.post(`${API_BASE_URL}/auth/refresh`, { refresh_token: refreshToken }, { timeout: REQUEST_TIMEOUT_MS });
      tokenStorage.set(data.data.access_token, data.data.refresh_token);
      lastRefreshedAccess = data.data.access_token;
      return data.data.access_token as string;
    } catch (error) {
      // Another tab refreshed meanwhile (refresh tokens rotate): its fresh
      // pair is already in storage — that is a live session, not a failure.
      const stored = tokenStorage.getRefresh();
      const access = tokenStorage.getAccess();
      if (stored && stored !== refreshToken && access) return access;
      if (isSessionRejection(error)) endSession(serverMessage(error));
      throw error;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: { "Content-Type": "application/json" },
  timeout: REQUEST_TIMEOUT_MS,
});

// A 401 from an AUTH endpoint means "those credentials are wrong", not
// "your session expired" — those reject normally and the form shows its
// own message. Logout never refreshes either.
const AUTH_ATTEMPT = /\/auth\/(login|register|refresh|logout|google|otp-login|otp\/|booking-access|forgot-password|reset-password|verify-phone)/;

apiClient.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const isAuthAttempt = AUTH_ATTEMPT.test(config.url || "");
  let token = tokenStorage.getAccess();
  // Refresh a token that's about to lapse BEFORE using it: endpoints that
  // also serve guests (the booking quote, the catalogue) treat an expired
  // token as "anonymous" instead of answering 401 — so a customer idle on
  // /app/book for 15 minutes was silently quoted as a stranger (first-wash
  // price, no plan) with nothing to trigger a refresh.
  if (token && !isAuthAttempt && token !== lastRefreshedAccess && tokenStorage.getRefresh() && tokenExpiresWithin(token, 60)) {
    try {
      token = await refreshSession();
    } catch {
      // Offline/5xx: send what we have — the 401 path below deals with it.
      // Refused: the session was ended; go on as a guest.
      token = tokenStorage.getAccess();
    }
  }
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  } else {
    // A retried request must not carry a session that has since ended.
    config.headers?.delete?.("Authorization");
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined;
    const isAuthAttempt = AUTH_ATTEMPT.test(originalRequest?.url || "");

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry && !isAuthAttempt) {
      originalRequest._retry = true;
      const sent = String(originalRequest.headers?.Authorization || "");
      const stored = tokenStorage.getAccess();
      // Another request (or tab) already refreshed since this one left.
      if (stored && sent && sent !== `Bearer ${stored}`) {
        return apiClient(originalRequest);
      }
      if (!sent) {
        // Sent without a session: nothing to refresh, nobody to sign out.
        return Promise.reject(error);
      }
      if (!tokenStorage.getRefresh()) {
        endSession(serverMessage(error));
        return Promise.reject(error);
      }
      try {
        await refreshSession();
      } catch (refreshError) {
        // Refused: refreshSession already ended the session. Offline/5xx/429:
        // the session stays — this request fails, the next one tries again.
        return Promise.reject(axios.isAxiosError(refreshError) ? refreshError : error);
      }
      return apiClient(originalRequest);
    }

    return Promise.reject(error);
  }
);

export interface ApiSuccess<T> {
  success: boolean;
  message: string;
  data: T;
}

export interface ApiPaginated<T> {
  success: boolean;
  message: string;
  data: T[];
  meta: { page: number; page_size: number; total: number; total_pages: number };
}

export interface ApiError {
  success: false;
  error_code: string;
  message: string;
  details?: Record<string, unknown>;
}

/** A 422 from the API says only "Request validation failed"; the useful
 *  part is the first field error inside details.errors (pydantic's own
 *  shape: loc + msg). Turns it into "Customer phone: Enter a valid
 *  10-digit mobile number" so a form shows what to fix, not a shrug. */
function validationMessage(data: ApiError): string | null {
  const errors = (data.details as { errors?: { loc?: unknown[]; msg?: string }[] } | undefined)?.errors;
  const first = Array.isArray(errors) ? errors[0] : undefined;
  if (!first?.msg) return null;
  const msg = first.msg.replace(/^Value error,\s*/i, "").replace(/^Assertion failed,\s*/i, "");
  const field = (first.loc || []).filter((p) => typeof p === "string" && !["body", "query", "path"].includes(p as string)).pop() as
    | string
    | undefined;
  if (!field) return msg;
  const label = field.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  return `${label}: ${msg.charAt(0).toLowerCase()}${msg.slice(1)}`;
}

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const raw = error.response?.data;
    // Only the API's own JSON error carries a message worth showing; a
    // proxy/load-balancer page (HTML 502/503) or an empty body doesn't.
    const data = raw && typeof raw === "object" ? (raw as ApiError) : undefined;
    if (data?.error_code === "VALIDATION_ERROR") {
      const detail = validationMessage(data);
      if (detail) return detail;
    }
    if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT") {
      return "This is taking too long — check your connection and try again.";
    }
    if (error.code === "ERR_CANCELED") return "Request cancelled.";
    if (!error.response) return "Can't reach the server — check your connection and try again.";
    const status = error.response.status;
    if (status === 429) return (typeof data?.message === "string" && data.message) || "Too many requests — please wait a moment and try again.";
    if (typeof data?.message === "string" && data.message) return data.message;
    if (status >= 500) return "Something went wrong on our side — please try again in a moment.";
    return "Something went wrong — please try again.";
  }
  return "Something went wrong";
}

/** HTTP status of a failed API call — undefined for a network failure
 * (nothing came back), which is worth retrying; a 4xx is the server's answer. */
export function getErrorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined;
}

/** Retry a read on a network blip / 5xx (up to `max` times), never on a
 * 4xx — "not found" / "not yours" won't change by asking again. */
export function retryUnlessClientError(max = 2) {
  return (failureCount: number, error: unknown) => {
    const status = getErrorStatus(error);
    return failureCount < max && !(status !== undefined && status >= 400 && status < 500);
  };
}

/** Machine-readable error_code from a failed API call (e.g.
 * "PHONE_NOT_VERIFIED") — lets a caller branch on a specific failure
 * reason instead of just displaying getErrorMessage()'s text. */
export function getErrorCode(error: unknown): string | undefined {
  if (axios.isAxiosError(error)) {
    return (error.response?.data as ApiError | undefined)?.error_code;
  }
  return undefined;
}
