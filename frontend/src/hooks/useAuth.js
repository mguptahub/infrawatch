import { createContext, useContext, useEffect, useState } from "react";
import { api } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(null); // { role, email, name, services }
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    checkSession();
    const onExpired = () => setAuth(null);
    window.addEventListener("session-expired", onExpired);
    return () => window.removeEventListener("session-expired", onExpired);
  }, []);

  async function checkSession() {
    try {
      const result = await api.getMe();
      setAuth(result);
    } catch {
      setAuth(null);
    } finally {
      setLoading(false);
    }
  }

  async function loginWithOTP(email, code) {
    const result = await api.verifyOTP(email, code);
    setAuth(result);
    return result;
  }

  async function logout() {
    try { await api.logout(); } catch { /* ignore */ }
    setAuth(null);
  }

  async function terminate() {
    try { await api.terminateSession(); } catch { /* ignore */ }
    setAuth(null);
  }

  async function switchRegion(region) {
    const result = await api.switchRegion(region);
    setAuth((prev) => ({ ...prev, region: result.region }));
  }

  return (
    <AuthContext.Provider value={{ auth, loginWithOTP, logout, terminate, switchRegion, loading }}>
      {loading ? (
        <div className="loading-screen">
          <div className="spinner" />
          <p>Initializing…</p>
        </div>
      ) : children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
