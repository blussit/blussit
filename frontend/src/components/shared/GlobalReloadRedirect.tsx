import { useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";

export function GlobalReloadRedirect() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    try {
      const navEntries = window.performance.getEntriesByType("navigation") as PerformanceNavigationTiming[];
      const isReload = 
        (navEntries.length > 0 && navEntries[0].type === "reload") ||
        (window.performance.navigation && window.performance.navigation.type === 1);

      if (isReload && location.pathname !== "/") {
        navigate("/", { replace: true });
      }
    } catch (e) {
      // Ignore performance API errors
    }
  }, []);

  return null;
}
