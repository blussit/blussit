import { Link } from "react-router-dom";
import { Clock } from "lucide-react";
import { SERVICES } from "../../../seo/content";
import { usePublicServices } from "../../../seo/useLiveServices";
import { INR, priceView, titleCase } from "../../../components/public/landing/shared";
import { BLUE, BookButtons, CtaBand, FaqList, JsonLd, MUTED, PageHero, SITE, SeoPage, Section, useBookHref } from "./SeoKit";

const path = "/services/add-ons";

/** What each add-on is, in plain words. Only what the booking rules guarantee:
 *  add-ons ride on a wash and are done in the same visit. Names, prices and
 *  minutes are always the live catalogue's. */
const COPY: Record<string, { heading: string; text: string }> = {
  "car-polish": { heading: "Car Polish At Home", text: "Add a car polish to your car wash and the captain does it in the same visit, at your doorstep." },
  "bike-polish": { heading: "Bike Polish At Home", text: "A bike body polish, added to your bike wash and done at home in the same visit." },
  "windshield-refill": { heading: "Windshield Cleaner Refill", text: "Add a windshield cleaner refill to your car wash — no separate trip to a shop or a service centre." },
  "engine-wash": { heading: "Engine Wash", text: "A quick engine wash, added to your car wash and done in the same visit." },
  "rat-spray": { heading: "Rat Spray", text: "Add a rat spray to your car wash and the captain applies it in the same visit." },
};

const FAQS = [
  { q: "Can I Book Car Polish Or Windshield Cleaner Refill On Its Own?", a: "Add-ons are booked together with a wash. Pick a Blussit wash, add the extras you want, and the captain does everything in one visit." },
  { q: "Can I Add Bike Polish To A Car Wash?", a: "Bike Polish goes with a bike wash, and the car add-ons — car polish, windshield cleaner refill, engine wash and rat spray — go with a car wash." },
  { q: "Where Do I See The Price?", a: "Every add-on shows its price on this page and again on the booking page, before you confirm. There is nothing to pay until you book." },
];

/** /services/add-ons — car polish, bike polish, windshield cleaner refill and the other extras. */
export default function AddOnsPage() {
  const all = usePublicServices();
  const bookHref = useBookHref();
  const addons = all.filter((s) => s.is_addon && COPY[s.slug]);

  return (
    <SeoPage
      path={path}
      crumbs={[
        { label: "Services", path: "/services" },
        { label: "Add-Ons", path },
      ]}
    >
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "ItemList",
          name: "Blussit car and bike wash add-ons in Indore",
          itemListElement: addons.map((a, i) => ({
            "@type": "ListItem",
            position: i + 1,
            item: {
              "@type": "Service",
              name: COPY[a.slug].heading,
              url: `${SITE}${path}`,
              provider: { "@id": `${SITE}/#organization` },
              areaServed: { "@type": "City", name: "Indore" },
              offers: { "@type": "Offer", price: priceView(a).final, priceCurrency: "INR" },
            },
          })),
        }}
      />
      <PageHero
        eyebrow="Add-Ons"
        title="Car Polish, Bike Polish & Windshield Cleaner At Home In Indore"
        intro="Add car polish, bike polish, a windshield cleaner refill, an engine wash or a rat spray to any Blussit wash. The captain does it all in the same visit, at your doorstep."
      >
        <BookButtons bookHref={bookHref} />
      </PageHero>

      <Section title="Choose Your Add-Ons" tint>
        {addons.length === 0 ? (
          <p className="text-[15px] leading-[1.7]" style={{ color: MUTED }}>
            Add-ons are shown here with live prices. Book any wash to add them.
          </p>
        ) : (
          <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {addons.map((a) => (
              <li key={a.id} className="flex flex-col rounded-[18px] border border-[#EDF0F5] bg-white p-5 shadow-[0_6px_20px_rgba(15,30,60,0.06)]">
                <h3 className="text-[19px] font-bold">{COPY[a.slug].heading}</h3>
                <p className="mt-1 text-[14px] leading-[1.55]" style={{ color: MUTED }}>
                  {COPY[a.slug].text}
                </p>
                <p className="mt-3 flex items-center gap-1.5 text-[13px]" style={{ color: MUTED }}>
                  <Clock className="h-4 w-4" aria-hidden="true" /> About {a.duration_minutes || 10} Mins
                </p>
                <p className="mt-auto pt-4 text-[24px] font-extrabold">{INR(priceView(a).final)}</p>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-6 text-[15px] leading-[1.7]" style={{ color: MUTED }}>
          Add-ons go with a wash. Choose from{" "}
          {SERVICES.map((s, i) => (
            <span key={s.slug}>
              <Link to={`/services/${s.slug}`} className="font-semibold underline" style={{ color: BLUE }}>
                {titleCase(s.name)}
              </Link>
              {i < SERVICES.length - 2 ? ", " : i === SERVICES.length - 2 ? " or " : ""}
            </span>
          ))}
          , or see{" "}
          <Link to="/services" className="font-semibold underline" style={{ color: BLUE }}>
            all car wash services in Indore
          </Link>
          .
        </p>
      </Section>

      <Section title="Questions About Add-Ons">
        <FaqList faqs={FAQS} />
      </Section>

      <CtaBand bookHref={bookHref} />
    </SeoPage>
  );
}
