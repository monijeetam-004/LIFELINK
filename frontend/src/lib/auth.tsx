import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, toApiError } from "./api";
import type { Role, User } from "./types";

export interface RegisterInput {
  full_name: string;
  email: string;
  password: string;
  role: Role;
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (input: RegisterInput) => Promise<User>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      // QA fix: /auth/session answers 200 for anonymous visitors too (no
      // 401 -> no browser-console error on every public page).
      const { data } = await api.get<{ authenticated: boolean; user: User | null }>("/auth/session");
      setUser(data.user);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // A 401 on any authenticated call after the initial load means the
  // session died server-side mid-use (see lib/api.ts's response
  // interceptor). Drop local user state immediately so guarded routes
  // react right away; the redirect-with-explanation lives in
  // <SessionExpiredListener/> (App.tsx), which needs router context.
  useEffect(() => {
    function onSessionExpired() {
      setUser(null);
    }
    window.addEventListener("lifelink:session-expired", onSessionExpired);
    return () => window.removeEventListener("lifelink:session-expired", onSessionExpired);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const { data } = await api.post<User>("/auth/login", { email, password });
      setUser(data);
      return data;
    } catch (err) {
      throw toApiError(err);
    }
  }, []);

  const register = useCallback(async (input: RegisterInput) => {
    try {
      const { data } = await api.post<User>("/auth/register", input);
      return data;
    } catch (err) {
      throw toApiError(err);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      /* even if the call fails, drop local state */
    }
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout, refresh }),
    [user, loading, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
