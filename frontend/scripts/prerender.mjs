// Pre-renders every public route in src/seo/pages.json to static HTML, so
// search engines get each page's title, description, canonical and content
// without running JavaScript (the SPA alone served one empty "Blussit" page
// at every URL). Runs at the end of `npm run build`:
//
//   dist/index.html            "/" — the landing page, fully rendered
//   dist/_pages/<route>.html   every other route in pages.json
//   dist/spa.html, 404.html    the untouched app shell for all other URLs
//
// vercel.json maps each route to its file and everything else to spa.html.
// Missing API data only warns (the page renders without it); a render that
// throws fails the build, so a broken page never ships quietly.

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");
const entry = path.join(root, "node_modules/.prerender/entry.js");
const pages = JSON.parse(await fs.readFile(path.join(root, "src/seo/pages.json"), "utf8")).pages;

// The API client reads the login token from storage on every request; the
// public calls made here are anonymous, so an empty store is exactly right.
const memoryStorage = () => {
  const items = new Map();
  return {
    getItem: (k) => (items.has(k) ? items.get(k) : null),
    setItem: (k, v) => items.set(k, String(v)),
    removeItem: (k) => items.delete(k),
    key: (i) => [...items.keys()][i] ?? null,
    clear: () => items.clear(),
    get length() {
      return items.size;
    },
  };
};
globalThis.localStorage ??= memoryStorage();
globalThis.sessionStorage ??= memoryStorage();

const { render, SERVICE_PATHS } = await import(pathToFileURL(entry).href);

const shell = await fs.readFile(path.join(dist, "index.html"), "utf8");
// The shell serves only private app routes (/app, /login…) and unknown
// URLs (404) — never a page for the index. Public pages are written from
// `shell` below and don't get this tag.
const privateShell = shell.replace("<head>", '<head>\n    <meta name="robots" content="noindex" />');
await fs.writeFile(path.join(dist, "spa.html"), privateShell);
await fs.writeFile(path.join(dist, "404.html"), privateShell);

// Every service page needs a title/description in pages.json, or it would
// fall back to the homepage's and compete with it.
const listed = new Set(pages.map((p) => p.path));
const unlisted = SERVICE_PATHS.filter((p) => !listed.has(p));
if (unlisted.length) {
  console.error(`prerender: add these to src/seo/pages.json: ${unlisted.join(", ")}`);
  process.exit(1);
}

// The shell's own per-page tags (homepage title/og) — replaced by each
// page's PageSeo output. Site-wide tags (og:image, JSON-LD…) stay.
const PER_PAGE_TAG =
  /\s*<title>[^<]*<\/title>|\s*<meta\s+(?:property="og:(?:title|description|url)"|name="twitter:(?:title|description)")[\s\S]*?\/>/g;
// React 19 renders <title>/<meta>/<link> first, ahead of the body.
const HOISTED = /^(?:<title>[\s\S]*?<\/title>|<meta\b[^>]*>|<link\b[^>]*>)/;

function splitHead(html) {
  const tags = [];
  let rest = html;
  for (let m = rest.match(HOISTED); m; m = rest.match(HOISTED)) {
    tags.push(m[0]);
    rest = rest.slice(m[0].length);
  }
  const head = tags
    // Images are in the HTML now — the browser finds them on its own, and
    // React's picks include below-the-fold ones that would steal bandwidth.
    // Only an image the page marked fetchPriority="high" (its LCP, e.g. a
    // service page's photo) keeps its preload, so it starts first.
    .filter((t) => !/^<link\b[^>]*rel="preload"/.test(t) || /fetchpriority="high"/i.test(t))
    // main.tsx drops these on boot; the app renders its own (see there).
    .map((t) => (t.startsWith("<title") || /rel="preload"/.test(t) ? t : t.replace(/\s*\/?>$/, ' data-prerender=""/>')))
    .join("\n    ");
  return { head, body: rest };
}

const json = (value) => JSON.stringify(value).replace(/</g, "\\u003c");

let warnings = 0;
for (const page of pages) {
  const { html, state, missing, full } = await render(page.path);
  if (missing.length) {
    warnings += 1;
    console.warn(`prerender: ${page.path} rendered without ${missing.join(", ")}`);
  }
  const { head, body } = splitHead(html);
  if (!head.includes("<title>")) throw new Error(`prerender: ${page.path} has no <title> — is PageSeo rendered?`);

  let doc = shell.replace(PER_PAGE_TAG, "").replace("</head>", `  ${head}\n  </head>`);
  if (full) {
    const data = state ? `<script>window.__BLUSSIT_QUERIES__=${json(state)}</script>` : "";
    doc = doc.replace('<div id="root"></div>', `<div id="root">${body}</div>${data}`);
  }
  const out = page.path === "/" ? path.join(dist, "index.html") : path.join(dist, "_pages", `${page.path.slice(1)}.html`);
  await fs.mkdir(path.dirname(out), { recursive: true });
  await fs.writeFile(out, doc);
  console.log(`prerender: ${page.path.padEnd(32)} ${full ? "full" : "head"}  ${(doc.length / 1024).toFixed(0)} KB`);
}
console.log(`prerender: ${pages.length} pages${warnings ? `, ${warnings} with missing data (see above)` : ""}`);
