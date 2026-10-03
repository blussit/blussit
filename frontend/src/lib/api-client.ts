import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

/**
 * Where the API lives. In a PRODUCTION build a missing VITE_API_BASE_URL is
 * a deployment mistake — the live API fallback keeps production builds
 * pointed at the canonical API subdomain when the hosting provider variable
 * is missing.
 */
const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL;
if (!configuredBaseUrl && import.meta.env.PROD) {
  console.error("VITE_API_BASE_URL is not set for this build — falling back to the live API.");
}
export const API_BASE_URL =
  configuredBaseUrl || (import.meta.env.PROD ? "https://api.blussit.com/api/v1" : "http://localhost:8000/api/v1");

export const TOKEN_KEY = "dvc_access_token";
export const REFRESH_KEY = "dvc_refresh_token";

export const tokenStorage = {
  getAccess: () => localStorage.getItem(TOKEN_KEY),
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
  set: (access: string, refresh: string) => {
    localStorage.setItem(TOKEN_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
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

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: { "Content-Type": "application/json" },
});

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenStorage.getAccess();
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let isRefreshing = false;
let pendingQueue: Array<() => void> = [];

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined;

    // A 401 from an AUTH endpoint means "those credentials are wrong", not
    // "your session expired". Running the session-expiry path on it did a
    // full `window.location.href` reload of /login, which wiped the React
    // state holding the error — so a wrong password looked like the page
    // just blinked, with no explanation at all. Let those reject normally
    // and the form shows its own message.
    const isAuthAttempt = /\/auth\/(login|register|refresh|google|otp-login|otp\/|booking-access|forgot-password|reset-password|verify-phone)/.test(
      originalRequest?.url || ""
    );

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry && !isAuthAttempt) {
      const refreshToken = tokenStorage.getRefresh();
      if (!refreshToken) {
        tokenStorage.clear();
        window.location.href = "/login";
        return Promise.reject(error);
      }

      originalRequest._retry = true;

      if (isRefreshing) {
        return new Promise((resolve) => {
          pendingQueue.push(() => resolve(apiClient(originalRequest)));
        });
      }

      isRefreshing = true;
      try {
        const { data } = await axios.post(`${API_BASE_URL}/auth/refresh`, { refresh_token: refreshToken });
        tokenStorage.set(data.data.access_token, data.data.refresh_token);
        pendingQueue.forEach((cb) => cb());
        pendingQueue = [];
        return apiClient(originalRequest);
      } catch (refreshError) {
        tokenStorage.clear();
        window.location.href = "/login";
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
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
    const data = error.response?.data as ApiError | undefined;
    if (data?.error_code === "VALIDATION_ERROR") {
      const detail = validationMessage(data);
      if (detail) return detail;
    }
    if (error.response?.status === 429) return data?.message || "Too many requests — please wait a moment and try again.";
    if (!error.response && error.code === "ERR_NETWORK") return "Can't reach the server — check your connection and try again.";
    return data?.message || error.message || "Something went wrong";
  }
  return "Something went wrong";
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
