import { Link } from "react-router-dom";
import { Check, Clock, Mail, MapPin, Phone } from "lucide-react";
import { BUSINESS, HOW_IT_WORKS, INDORE_FAQS, SERVICES } from "../../../seo/content";
import { liveFor, usePublicServices } from "../../../seo/useLiveServices";
import { BLUE, BookButtons, CtaBand, FaqList, JsonLd, MUTED, PageHero, Price, SITE, SeoPage, Section, useBookHref } from "./SeoKit";

const WHY = [
  "No driving to a car washing service centre, no waiting in a queue",
  "ID-verified captains, with before and after photos saved on every booking",
  "Pay in cash after the wash, or online — your choice on most services",
  "Waterless option for basements and covered parking",
  "One visit for every car and bike at the same address",
  "Monthly plans for regular washes and daily society car cleaning",
];

const CONTACT = [
  { icon: Phone, label: BUSINESS.phoneDisplay, href: BUSINESS.phoneHref },
  { icon: Mail, label: BUSINESS.email, href: `mailto:${BUSINESS.email}` },
  { icon: MapPin, label: BUSINESS.address, href: BUSINESS.mapsHref },
  { icon: Clock, label: BUSINESS.hours },
];

/** /doorstep-car-wash-indore — the local page for "car wash near me / at home in Indore" searches. */
export default function IndoreCarWashPage() {
  const all = usePublicServices();
  const bookHref = useBookHref();
  const path = "/doorstep-car-wash-indore";

  return (
    <SeoPage path={path} crumbs={[{ label: "Doorstep Car Wash In Indore", path }]}>
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "Service",
          name: "Doorstep car wash in Indore",
          serviceType: "Car wash at home",
          url: `${SITE}${path}`,
          provider: { "@id": `${SITE}/#organization` },
          areaServed: { "@type": "City", name: "Indore" },
          hasOfferCatalog: {
            "@type": "OfferCatalog",
            name: "Doorstep car and bike wash",
            itemListElement: SERVICES.map((s) => ({ "@type": "Offer", itemOffered: { "@type": "Service", name: s.name, url: `${SITE}/services/${s.slug}` } })),
          },
        }}
      />
      <PageHero
        eyebrow="Indore, Madhya Pradesh"
        title="Doorstep Car Wash In Indore"
        intro="Looking for a car wash near you? Blussit comes to you instead. We wash your car at home, at the office or in your society parking — across Indore, 7 days a week."
      >
        <BookButtons bookHref={bookHref} />
      </PageHero>

      <Section title="Car Wash At Home, Done Properly" tint>
        <div className="grid gap-6 text-[15px] leading-[1.7] lg:grid-cols-2" style={{ color: MUTED }}>
          <p>
            Getting a car washed in Indore usually means driving to a washing centre, waiting your turn and driving back. With Blussit you book a slot online
            or on WhatsApp, and a trained captain arrives at your address with everything needed — shampoo, foam, cloths and equipment. Your car is
            washed right where it is parked while you carry on with your day.
          </p>
          <p>
            Choose a quick exterior Jet Wash, a foam Star Wash with an interior vacuum, a waterless wash for basement parking, or a full interior deep
            clean. Bikes and scooters are washed at home too. Prices are shown upfront before you book, and you can pay the captain in cash after the
            wash on most services.
          </p>
        </div>
      </Section>

      <Section title="How It Works">
        <ol className="grid gap-4 md:grid-cols-3">
          {HOW_IT_WORKS.map((step, i) => (
            <li key={step.title} className="rounded-[18px] border border-[#EDF0F5] p-5">
              <span className="font-mono-num text-[13px] font-extrabold" style={{ color: BLUE }}>
                Step {i + 1}
              </span>
              <p className="mt-1 text-[17px] font-bold">{step.title}</p>
              <p className="mt-1.5 text-[14.5px] leading-[1.6]" style={{ color: MUTED }}>
                {step.text}
              </p>
            </li>
          ))}
        </ol>
      </Section>

      <Section title="Services And Prices In Indore" tint>
        <ul className="divide-y divide-[#E4EBF5] overflow-hidden rounded-[18px] border border-[#D9E8FF] bg-white">
          {SERVICES.map((content) => {
            const live = liveFor(content, all);
            return (
              <li key={content.slug} className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
                <div className="min-w-0">
                  <Link to={`/services/${content.slug}`} className="text-[16px] font-bold hover:underline">
                    {content.name}
                  </Link>
                  <p className="text-[13.5px]" style={{ color: MUTED }}>
                    {live.includes.join(" · ")} · About {live.minutes} Mins
                  </p>
                </div>
                <Price price={live.price} original={live.original} varies={live.varies} />
              </li>
            );
          })}
        </ul>
        <p className="mt-4 text-[14px]" style={{ color: MUTED }}>
          <Link to="/services" className="font-semibold underline" style={{ color: BLUE }}>
            Compare All Services
          </Link>{" "}
          ·{" "}
          <Link to="/plans" className="font-semibold underline" style={{ color: BLUE }}>
            Monthly Car Wash Plans
          </Link>
        </p>
      </Section>

      <Section
        title="Where We Wash In Indore"
        intro="Every colony in Indore — at your home, office or society parking. Drop a pin while booking and the captain comes to that exact spot. If a distance charge applies to your address, you see it before you confirm."
      >
        <Link
          to={bookHref}
          className="inline-flex h-11 items-center gap-2 rounded-full border border-[#D6DEEA] bg-white px-5 text-[15px] font-semibold hover:border-[#0E1A33]"
        >
          <MapPin className="h-4 w-4" style={{ color: BLUE }} aria-hidden="true" /> Book For Your Address
        </Link>
      </Section>

      <Section title="Why Choose Blussit" tint>
        <ul className="grid gap-3 sm:grid-cols-2">
          {WHY.map((w) => (
            <li key={w} className="flex items-start gap-3 rounded-[16px] bg-white p-4 text-[15px] font-semibold">
              <Check className="mt-0.5 h-5 w-5 shrink-0" style={{ color: BLUE }} strokeWidth={3} aria-hidden="true" /> {w}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Questions About Car Wash At Home In Indore">
        <FaqList faqs={INDORE_FAQS} />
      </Section>

      <Section title="Contact Blussit" tint>
        <ul className="grid gap-3 sm:grid-cols-2">
          {CONTACT.map(({ icon: Icon, label, href }) => (
            <li key={label} className="flex items-center gap-3 rounded-[16px] bg-white p-4 text-[15px] font-semibold">
              <Icon className="h-5 w-5 shrink-0" style={{ color: BLUE }} aria-hidden="true" />
              {href ? (
                <a href={href} className="hover:underline" {...(href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}>
                  {label}
                </a>
              ) : (
                label
              )}
            </li>
          ))}
        </ul>
        <p className="mt-5 max-w-3xl text-[14.5px] leading-[1.7]" style={{ color: MUTED }}>
          Blussit is spelled B-L-U-S-S-I-T. If you searched for {BUSINESS.spellings.join(", ")} — you're in the right place.
        </p>
      </Section>

      <CtaBand bookHref={bookHref} title="Book A Car Wash Near You" text="Pick a slot between 7 AM and 7 PM, any day of the week. A verified captain comes to your door." />
    </SeoPage>
  );
}
