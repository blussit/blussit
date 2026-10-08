import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HelmetProvider } from "react-helmet-async";
import "./index.css";
import App from "./App";
import { reloadOnStaleChunks } from "./lib/staleChunkReload";
import { installContactTracking } from "./lib/metaPixel";

reloadOnStaleChunks();
installContactTracking();

// Pre-rendered pages carry their description/canonical/og tags in the HTML
// for crawlers that don't run JavaScript. The app renders its own copies
// (PageSeo), so drop these first — otherwise a later in-app navigation would
// leave the first page's canonical behind next to the new one.
document.head.querySelectorAll("[data-prerender]").forEach((node) => node.remove());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HelmetProvider>
      <App />
    </HelmetProvider>
  </StrictMode>
);
