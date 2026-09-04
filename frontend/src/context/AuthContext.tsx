import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { authApi } from "../api/auth";
import { tokenStorage } from "../lib/api-client";
import { liveSocket } from "../lib/socket";
import type { User } from "../types";

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (identifier: string, password: string) => Promise<User>;
  register: (payload: { full_name: string; email?: string; phone?: string; password: string }) => Promise<User>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const loadUser = async () => {
    const params = new URLSearchParams(window.location.search);
    const urlAccess = params.get("access_token");
    const urlRefresh = params.get("refresh_token");
    if (urlAccess && urlRefresh) {
      tokenStorage.set(urlAccess, urlRefresh);
      window.history.replaceState({}, document.title, window.location.pathname);
    }

    const token = tokenStorage.getAccess();
    if (!token) {
      setIsLoading(false);
      return;
    }
    try {
      const me = await authApi.me();
      setUser(me);
    } catch {
      tokenStorage.clear();
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadUser();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = async (identifier: string, password: string) => {
    const result = await authApi.login({ identifier, password });
    tokenStorage.set(result.access_token, result.refresh_token);
    setUser(result.user);
    return result.user;
  };

  const register = async (payload: { full_name: string; email?: string; phone?: string; password: string }) => {
    const result = await authApi.register(payload);
    tokenStorage.set(result.access_token, result.refresh_token);
    setUser(result.user);
    return result.user;
  };

  const logout = () => {
    tokenStorage.clear();
    liveSocket.disconnect();
    setUser(null);
    window.location.href = "/";
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, isAuthenticated: !!user, login, register, logout, refreshUser: loadUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
