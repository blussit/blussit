# SEO — Keywords, Pages And Off-Site Checklist

Every search phrase we want to rank for, and the page built to rank for it.
Titles and descriptions live in `frontend/src/seo/pages.json`; page copy in
`frontend/src/seo/content.ts`. All public pages are pre-rendered to HTML at
build time (`frontend/scripts/prerender.mjs`), so Google reads them without
running JavaScript.

## Why "blussit" ranked but "blussit car wash" didn't

Until this change every URL returned the same empty page titled "Blussit" —
no description, no text, no links. The words "car wash", "Indore",
"doorstep" only appeared after JavaScript ran and the API answered. Google
could match the brand name and little else, and the site had only 6
indexable URLs with no page about any one service or about Indore.

## Keyword map

| Cluster | Search phrases | Page |
|---|---|---|
| Brand | blussit, blussit car wash, blussit indore, blussit doorstep service, blussit services, blussit price, blussit contact number, blussit reviews, blussit booking, blussit app | `/` and `/doorstep-car-wash-indore` |
| Brand spellings | blushit, blusit, blasit, blashit, bluss it, blush it, blussitt | `/doorstep-car-wash-indore` "Contact Blussit" (said once, plainly) + `alternateName` in the site JSON-LD |
| Local / near me | car wash near me, car wash in indore, car wash indore, doorstep car wash, doorstep car wash indore, door step car washing, car wash at home, car wash at home indore, home car wash service, home car services, car wash at my place, mobile car wash indore, car cleaning service at home, car cleaning service indore, car wash service near me, car washing service center near me, best car wash in indore, premium car wash indore, premium car services indore, car wash booking online | `/doorstep-car-wash-indore` and `/` |
| Locality | car wash vijay nagar, car wash palasia, car wash bhawarkuwa … (Indore colony + "car wash") | `/doorstep-car-wash-indore` says we serve every colony in Indore (founder, 2026-10-07). Never a page per colony. |
| Jet Wash | jet wash, jet car wash, jet wash near me, exterior car wash, quick car wash, car wash 149 | `/services/jet-wash` |
| Star Wash / foam | star wash, foam wash, foam car wash, foam car wash at home, car shampoo wash at home | `/services/star-wash` |
| Waterless | waterless car wash, waterless car wash indore, car wash without water, dry car wash, eco friendly car wash, car wash basement parking | `/services/waterless-car-wash` |
| Deep cleaning | car deep cleaning, car deep cleaning indore, car interior cleaning, car interior cleaning at home, car seat cleaning, car mat cleaning, car detailing at home, car interior detailing | `/services/car-deep-cleaning` |
| Bike | bike wash at home, bike wash near me, bike wash indore, scooty wash at home, two wheeler wash, bike foam wash | `/services/bike-wash` |
| Price intent | car wash price indore, car wash charges at home, doorstep car wash price, car deep cleaning price, bike wash price | `/services` |
| Plans | monthly car wash plan, car wash subscription, monthly car cleaning package, daily car cleaning service, car wash for housing society, society car wash indore, apartment car wash, fleet car washing | `/plans` |
| Booking | book car wash online, car wash booking indore, car wash appointment | `/book` |

Rules that keep this working:

- **One page per intent.** Don't create near-copies (e.g. a page per colony
  with the same text) — Google treats those as doorway pages and can demote
  the whole site.
- **Only verified facts.** Address = the office, 88 Shivampuri Colony,
  Bhawarkuwa, Indore 452001 (`BUSINESS` in `content.ts`, footer, JSON-LD in
  `index.html` — keep identical). Hours 07:00–19:00. Coverage: every colony
  in Indore; the exact pin is checked by the service zones at booking.
- **No hidden or stuffed text.** Each phrase appears where a person would
  naturally write it. Misspellings are mentioned once, on the Indore page.
- **New service = new page.** Add it to `SERVICES` in `content.ts` and give
  it a `pages.json` entry — the build fails if the entry is missing.

## Off-site checklist — this is most of "near me"

For "car wash near me" and the Google Maps pack, the website is only part of
the signal. These need someone with access to the business accounts:

1. **Google Business Profile** — the biggest single lever for local searches.
   - Business name exactly "Blussit"; primary category **Car wash**, add
     **Car detailing service**.
   - Set it up as a **service-area business** covering all of Indore, with
     the office address below.
   - Add every service with its price, opening hours (7 AM – 7 PM), the
     website link, and real photos of captains at work. Post weekly.
   - Name, address and phone must match the website **exactly**: Blussit,
     88 Shivampuri Colony, Bhawarkuwa, Indore 452001, +91 89622 88774.
     The website uses only this address (footer, Indore page, JSON-LD).
2. **Reviews** — ask every customer for a Google review after the wash
   (WhatsApp message with the review link). Review count and rating are a top
   local ranking factor.
3. **Google Search Console** — verify `blussit.com` (DNS record), submit
   `https://blussit.com/sitemap.xml`, then use URL Inspection → Request
   Indexing on each new page. Watch the Performance report for which phrases
   bring impressions.
4. **Bing Webmaster Tools** — import from Search Console in one click.
5. **Listings (citations)** with the same name, address and phone: Justdial,
   Sulekha, Bing Places, Apple Business Connect, magicpin, Facebook page,
   Instagram bio link.
6. **Links from Indore sites** — housing society / RWA partners, local news
   or blogs, Instagram collaborators linking to `blussit.com`.

Expect movement over weeks, not days: Google has to recrawl, and a new site
earns local ranking through reviews and listings as much as through pages.
