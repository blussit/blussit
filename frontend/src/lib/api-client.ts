import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

/**
 * Where the API lives. In a PRODUCTION build a missing VITE_API_BASE_URL is
 * a deployment mistake — falling back to localhost there would ship a site
 * that is broken for every visitor while looking fine to the developer who
 * built it. Same-origin "/api/v1" at least works behind a reverse proxy,
 * and the console error names the real problem.
 */
const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL;
if (!configuredBaseUrl && import.meta.env.PROD) {
  console.error("VITE_API_BASE_URL is not set for this build — falling back to same-origin /api/v1.");
}
export const API_BASE_URL =
  configuredBaseUrl || (import.meta.env.PROD ? "/api/v1" : "http://localhost:8000/api/v1");

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

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as ApiError | undefined;
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
