import { Helmet } from "react-helmet-async";
import seoConfig from "../../seo/pages.json";

/**
 * Per-route title/description/canonical, sourced from the single
 * src/seo/pages.json list (also used to generate sitemap.xml — see
 * scripts/generate-sitemap.mjs — so both stay in sync automatically).
 *
 * index.html deliberately has NO static <title>/description/canonical of
 * its own anymore: every route, including "/", renders one through here.
 * Two conflicting canonical tags on a page is something Google explicitly
 * warns can make it ignore BOTH — so there must only ever be one, and
 * react-helmet-async doesn't know about (or remove) tags baked into the
 * static HTML, only tags it manages itself. Splitting "one static
 * homepage-flavoured tag, overridden per-route by Helmet" would leave that
 * exact double-canonical problem after hydration; having every route go
 * through Helmet avoids it entirely.
 *
 * Open Graph/Twitter image and site-level tags DO stay static in
 * index.html — link-preview scrapers (WhatsApp, Facebook, iMessage, ...)
 * generally don't execute JavaScript, so anything Helmet-only would never
 * reach them. This component adds matching og:title/description/url on
 * top for the crawlers that DO run JS, which is pure upside.
 */
export function PageSeo({ path }: { path: string }) {
  const entry = seoConfig.pages.find((p) => p.path === path);
  const title = entry?.title ?? seoConfig.defaultTitle;
  const description = entry?.description ?? seoConfig.defaultDescription;
  const url = `${seoConfig.siteUrl}${path === "/" ? "/" : path}`;

  return (
    <Helmet>
      <title>{title}</title>
      <meta name="description" content={description} />
      <link rel="canonical" href={url} />
      <meta property="og:title" content={title} />
      <meta property="og:description" content={description} />
      <meta property="og:url" content={url} />
      <meta name="twitter:title" content={title} />
      <meta name="twitter:description" content={description} />
    </Helmet>
  );
}
