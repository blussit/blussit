/**
 * "Continue with Google" via Google Identity Services. The button hands
 * us a signed ID token; ONLY the backend's verification of it counts.
 * Renders nothing when Google sign-in isn't configured.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { googleAuthApi } from "../../api/auth";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage, tokenStorage } from "../../lib/api-client";
import { roleHomePath } from "../../lib/roleHome";

import "../../lib/googleMaps"; // shares the window.google declaration

let gsiLoader: Promise<boolean> | null = null;

function ensureGsi(): Promise<boolean> {
  if (!gsiLoader) {
    gsiLoader = new Promise((resolve) => {
      if (window.google?.accounts?.id) return resolve(true);
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.onload = () => resolve(true);
      script.onerror = () => resolve(false);
      document.head.appendChild(script);
    });
  }
  return gsiLoader;
}

export function GoogleSignInButton() {
  const slotRef = useRef<HTMLDivElement>(null);
  const { refreshUser } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const cfg = await googleAuthApi.config().catch(() => ({ enabled: false, client_id: null }));
      if (cancelled || !cfg.enabled || !cfg.client_id) return;
      if (!(await ensureGsi()) || !window.google?.accounts?.id || !slotRef.current) return;
      window.google.accounts.id.initialize({
        client_id: cfg.client_id,
        callback: async (response: { credential: string }) => {
          try {
            const result = await googleAuthApi.login(response.credential);
            tokenStorage.set(result.access_token, result.refresh_token);
            const me = await refreshUser();
            navigate(roleHomePath(me?.role), { replace: true });
          } catch (err) {
            setError(getErrorMessage(err));
          }
        },
      });
      window.google.accounts.id.renderButton(slotRef.current, {
        theme: "outline",
        size: "large",
        width: 320,
        text: "continue_with",
        shape: "pill",
      });
      setVisible(true);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={visible ? "space-y-2" : "hidden"}>
      <div className="relative my-1 flex items-center gap-3">
        <span className="h-px flex-1 bg-gray-200" />
        <span className="text-xs text-[var(--color-text-secondary)]">or</span>
        <span className="h-px flex-1 bg-gray-200" />
      </div>
      <div ref={slotRef} className="flex justify-center" />
      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
    </div>
  );
}
