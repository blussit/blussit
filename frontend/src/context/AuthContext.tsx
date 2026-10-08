import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import axios from "axios";
import { authApi } from "../api/auth";
import { API_BASE_URL, REQUEST_TIMEOUT_MS, TOKEN_KEY, endSession, getErrorStatus, onSessionEnd, tokenStorage, tokenSubject } from "../lib/api-client";
import { liveSocket } from "../lib/socket";
import type { User } from "../types";

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** A saved session couldn't be checked (offline, server down) — the
   *  tokens are kept; protected pages show "try again" instead of /login. */
  sessionUnverified: boolean;
  /** Why the last session ended, when the server said (e.g. "Your account
   *  has been suspended…") — shown on the login page. */
  sessionNotice: string | null;
  login: (identifier: string, password: string) => Promise<User>;
  register: (payload: { full_name: string; email?: string; phone?: string; password: string; guest?: boolean }) => Promise<User>;
  logout: () => void;
  refreshUser: () => Promise<User | null>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  // Nothing to check without a saved token — a first-time visitor is a
  // guest from the very first render (no loader flash on public pages).
  const [isLoading, setIsLoading] = useState(() => !!tokenStorage.getAccess());
  const [sessionUnverified, setSessionUnverified] = useState(false);
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);
  const userRef = useRef<User | null>(null);
  userRef.current = user;

  /**
   * Who is signed in, from GET /auth/me. Silent by design: a stale session
   * on the homepage, /book or a service page must never send a visitor to
   * /login (only ProtectedRoute does that, for the portals). The session is
   * dropped only when the server refuses it; a network blip, timeout or 5xx
   * keeps the tokens and the user already loaded.
   */
  const loadUser = useCallback(async (): Promise<User | null> => {
    if (!tokenStorage.getAccess()) {
      setUser(null);
      setSessionUnverified(false);
      setIsLoading(false);
      return null;
    }
    try {
      const me = await authApi.me();
      setUser(me);
      setSessionUnverified(false);
      setSessionNotice(null);
      return me;
    } catch (error) {
      const status = getErrorStatus(error);
      if (!tokenStorage.getAccess() || status === 401 || status === 403) {
        // The server refused this session (the API layer may already have
        // cleared it after a refused refresh).
        if (tokenStorage.getAccess()) endSession(null);
        setUser(null);
        setSessionUnverified(false);
        return null;
      }
      // Offline / timeout / 5xx: keep everything; say so where it matters.
      setSessionUnverified(!userRef.current);
      return userRef.current;
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUser();
  }, [loadUser]);

  // The API layer ended the session (a refused token refresh): drop the
  // user here; ProtectedRoute redirects portal pages to /login, public
  // pages simply carry on as a guest.
  useEffect(
    () =>
      onSessionEnd((notice) => {
        liveSocket.disconnect();
        setUser(null);
        setSessionUnverified(false);
        setSessionNotice(notice);
        setIsLoading(false);
      }),
    []
  );

  // A session we couldn't verify (offline at boot): try again as soon as
  // the connection is back.
  useEffect(() => {
    if (!sessionUnverified) return;
    const retry = () => void loadUser();
    window.addEventListener("online", retry);
    const timer = window.setTimeout(retry, 15_000);
    return () => {
      window.removeEventListener("online", retry);
      window.clearTimeout(timer);
    };
  }, [sessionUnverified, loadUser]);

  // Another tab signed out, or signed in as someone else: this tab must not
  // keep acting as the previous person (its screens, drafts and cached data
  // are theirs). A refresh in another tab (same person) changes nothing.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== TOKEN_KEY && event.key !== null) return;
      const nextId = tokenSubject(tokenStorage.getAccess());
      const currentId = userRef.current?.id ?? null;
      if (nextId === currentId) return;
      if (currentId) {
        window.location.reload();
        return;
      }
      if (nextId) void loadUser();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [loadUser]);

  const login = async (identifier: string, password: string) => {
    const result = await authApi.login({ identifier, password });
    tokenStorage.set(result.access_token, result.refresh_token);
    setSessionNotice(null);
    setSessionUnverified(false);
    setUser(result.user);
    return result.user;
  };

  const register = async (payload: { full_name: string; email?: string; phone?: string; password: string; guest?: boolean }) => {
    const result = await authApi.register(payload);
    tokenStorage.set(result.access_token, result.refresh_token);
    setSessionNotice(null);
    setSessionUnverified(false);
    setUser(result.user);
    return result.user;
  };

  const logout = () => {
    // Server-side first (kills every refresh token via token_version) —
    // best-effort, the local clear must never wait on a flaky network. The
    // token is captured here and sent directly: the request would otherwise
    // leave after the clear below and reach the server without it.
    const access = tokenStorage.getAccess();
    if (access) {
      axios
        .post(`${API_BASE_URL}/auth/logout`, null, { headers: { Authorization: `Bearer ${access}` }, timeout: REQUEST_TIMEOUT_MS })
        .catch(() => {});
    }
    tokenStorage.clear();
    liveSocket.disconnect();
    setUser(null);
    window.location.href = "/";
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        sessionUnverified,
        sessionNotice,
        login,
        register,
        logout,
        refreshUser: loadUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
