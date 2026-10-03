import { useLayoutEffect } from "react";
import "./customerTheme.css";

/**
 * Theme v2 for a customer-facing screen: puts `cust-v2` on <body> while
 * mounted (and `active`), so the page and anything it portals or mounts at
 * the app root (modals, toasts, the confirm box) share the blue/navy look.
 * See customerTheme.css.
 */
export function useCustomerTheme(active = true) {
  useLayoutEffect(() => {
    if (!active) return;
    document.body.classList.add("cust-v2");
    return () => document.body.classList.remove("cust-v2");
  }, [active]);
}
