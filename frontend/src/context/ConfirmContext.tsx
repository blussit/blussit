import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

export interface ConfirmOptions {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** "danger" (red confirm button) for destructive actions like delete —
   * the default for everything replacing a delete confirm(). */
  tone?: "danger" | "default";
}

interface ConfirmRequest extends ConfirmOptions {
  resolve: (confirmed: boolean) => void;
}

interface ConfirmContextValue {
  request: ConfirmRequest | null;
  confirm: (options: ConfirmOptions | string) => Promise<boolean>;
  respond: (confirmed: boolean) => void;
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null);

/**
 * In-app replacement for window.confirm()/confirm() — those render as a
 * browser-chrome popup (the URL bar shows "localhost:5173 says…") that
 * looks like a security warning, not part of the product, and can't be
 * styled or blocked from being auto-dismissed by strict browser settings.
 * This renders a real Modal instead, driven by one shared instance
 * (<ConfirmDialog/>, mounted once in App.tsx) so every call site just
 * awaits a promise exactly like the native version did.
 *
 * Usage: const confirmed = await confirm({ title: `Delete "${x.name}"?`, tone: "danger" });
 * if (!confirmed) return;
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  // Guards against a stray resolve() firing after a newer request has
  // already replaced it (e.g. two confirm() calls fired in quick
  // succession) — only the request object currently in state may resolve.
  const activeRequest = useRef<ConfirmRequest | null>(null);

  const confirm = useCallback((options: ConfirmOptions | string) => {
    const normalized: ConfirmOptions = typeof options === "string" ? { title: options } : options;
    return new Promise<boolean>((resolve) => {
      const req: ConfirmRequest = { ...normalized, resolve };
      activeRequest.current = req;
      setRequest(req);
    });
  }, []);

  const respond = useCallback((confirmed: boolean) => {
    if (activeRequest.current) {
      activeRequest.current.resolve(confirmed);
      activeRequest.current = null;
    }
    setRequest(null);
  }, []);

  return <ConfirmContext.Provider value={{ request, confirm, respond }}>{children}</ConfirmContext.Provider>;
}

/** Returns just the `confirm()` function — what every call site needs. */
export function useConfirm(): (options: ConfirmOptions | string) => Promise<boolean> {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used inside a ConfirmProvider");
  return ctx.confirm;
}

/** Internal — only <ConfirmDialog/> should use this to read/drive the
 * current request. Everything else should use useConfirm(). */
export function useConfirmDialogState() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirmDialogState must be used inside a ConfirmProvider");
  return { request: ctx.request, respond: ctx.respond };
}
