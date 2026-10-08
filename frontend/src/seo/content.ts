/**
 * Copy for the search-landing pages (/services, /services/:slug,
 * /doorstep-car-wash-indore, /plans). Titles and meta descriptions
 * live in pages.json next to every other route's; this file is the page
 * BODY. Prices are never written here as facts — the pages show the live
 * catalogue price (useLiveServices) and these numbers are only the fallback
 * while it loads. Keep every sentence true to how the service actually
 * works: these pages are what Google ranks us on.
 */

export const BUSINESS = {
  name: "Blussit",
  phoneDisplay: "+91 89622 88774",
  phoneHref: "tel:+918962288774",
  email: "contact.blussit@gmail.com",
  city: "Indore",
  // The office — the one address used everywhere (footer, site JSON-LD in
  // index.html, Google Business Profile). Keep all of them identical.
  street: "88 Shivampuri Colony, Bhawarkuwa",
  pin: "452001",
  address: "88 Shivampuri Colony, Bhawarkuwa, Indore 452001",
  mapsHref: "https://www.google.com/maps/search/?api=1&query=88+Shivampuri+Colony%2C+Bhawarkuwa%2C+Indore+452001",
  hours: "7:00 AM – 7:00 PM, All 7 Days",
  // How people actually type the name — said once, plainly, on the Indore page.
  spellings: ["Blushit", "Blusit", "Bluss It", "Blush It", "Blasit"],
};

export interface Faq {
  q: string;
  a: string;
}

export interface ServiceContent {
  /** URL slug under /services. */
  slug: string;
  /** Live catalogue slug — price, includes, booking deep link. */
  apiSlug: string;
  /** Extra live slugs priced on the page (e.g. the two-bike combo). */
  variantSlugs?: string[];
  name: string;
  h1: string;
  tagline: string;
  intro: string[];
  includesFallback: string[];
  minutesFallback: number;
  priceFallback: number;
  bestFor: string[];
  keepReady: string;
  faqs: Faq[];
  related: string[];
  image: { src: string; width: number; height: number };
  vehicle: "car" | "bike";
}

const KEEP_READY_WATER =
  "Park the vehicle where it can be washed safely, with a water tap and a power point nearby. The captain brings the shampoo, foam, cloths and equipment.";

export const SERVICES: ServiceContent[] = [
  {
    slug: "jet-wash",
    apiSlug: "jet-wash",
    name: "Jet Wash",
    h1: "Jet Wash At Your Doorstep In Indore",
    tagline: "Our quickest exterior car wash — foam wash and tyre polish, done where your car is parked.",
    intro: [
      "Jet Wash is the fastest way to get the dust of Indore's roads off your car without driving to a car washing centre. A Blussit captain comes to your home, office or society parking and gives the outside of your car a foam wash, then finishes the tyres with polish.",
      "It takes about half an hour, so it fits into a morning before work or an evening at home. It is an exterior wash only — if you want the inside vacuumed and the dashboard polished too, pick Star Wash.",
    ],
    includesFallback: ["Exterior Foam Wash", "Tyre Polish"],
    minutesFallback: 30,
    priceFallback: 149,
    bestFor: ["Regular upkeep between deeper washes", "A clean car before a trip or an occasion", "Cars parked outdoors that collect dust quickly"],
    keepReady: KEEP_READY_WATER,
    faqs: [
      { q: "Does Jet Wash Clean The Inside Of The Car?", a: "No. Jet Wash covers the outside of the car and the tyres. For an interior vacuum and dashboard polish as well, choose Star Wash; for seats, mats and floor, choose Deep Cleaning." },
      { q: "How Long Does A Jet Wash Take?", a: "About 30 minutes per car, depending on how dirty it is." },
      { q: "Can I Book Jet Wash For More Than One Car?", a: "Yes. Add every car to the same booking and the captain washes them one after another in the same visit." },
    ],
    related: ["star-wash", "waterless-car-wash", "car-deep-cleaning"],
    image: { src: "/img/svc-jet-960.webp", width: 960, height: 530 },
    vehicle: "car",
  },
  {
    slug: "star-wash",
    apiSlug: "star-wash",
    name: "Star Wash",
    h1: "Star Wash — Foam Car Wash At Home In Indore",
    tagline: "A foam wash outside, a vacuum and dashboard polish inside — our most popular car wash at home.",
    intro: [
      "Star Wash is a complete everyday car wash at your doorstep. The captain foam washes the exterior, vacuums the cabin and polishes the dashboard, so the car looks and feels clean inside and out.",
      "Book it every week or two to keep the car clean inside and out. You don't drive anywhere or wait in a queue — the car is washed right where it is parked.",
    ],
    includesFallback: ["Exterior Foam Wash", "Interior Vacuum", "Dashboard Polish"],
    minutesFallback: 45,
    priceFallback: 349,
    bestFor: ["A regular inside-and-out clean", "Family cars that get used every day", "Keeping the cabin fresh between deep cleans"],
    keepReady: KEEP_READY_WATER,
    faqs: [
      { q: "What Is The Difference Between Star Wash And Jet Wash?", a: "Jet Wash cleans the outside of the car and the tyres. Star Wash adds an interior vacuum and a dashboard polish on top of the exterior foam wash." },
      { q: "How Long Does A Star Wash Take?", a: "About 45 minutes per car." },
      { q: "Is There A Monthly Plan For Regular Washes?", a: "Yes. A Monthly Pass gives one car a set number of washes for the month; the price depends on your car type and the wash you choose. See our monthly plans for the current prices." },
    ],
    related: ["jet-wash", "car-deep-cleaning", "waterless-car-wash"],
    image: { src: "/img/svc-star-960.webp", width: 960, height: 516 },
    vehicle: "car",
  },
  {
    slug: "waterless-car-wash",
    apiSlug: "waterless-service",
    name: "Waterless Car Wash",
    h1: "Waterless Car Wash In Indore",
    tagline: "A clean car with no water connection needed — ideal for basements, covered parking and apartments.",
    intro: [
      "Our Waterless Service cleans the exterior of your car without a hose or a bucket, then vacuums the interior and polishes the dashboard. Because it needs no water connection, it works in basement parking, covered stilt parking and anywhere a regular wash would leave a mess.",
      "It also saves water — a real concern in Indore's summers. All you need is a shaded or covered spot for the car.",
    ],
    includesFallback: ["Waterless Exterior Clean", "Interior Vacuum", "Dashboard Polish"],
    minutesFallback: 45,
    priceFallback: 319,
    bestFor: ["Basement or covered parking with no water tap", "Societies that don't allow washing with water", "Saving water without skipping a wash"],
    keepReady: "Park the car in a shaded or covered spot. No water connection is needed — the captain brings everything.",
    faqs: [
      { q: "Is A Waterless Car Wash Safe For The Paint?", a: "Yes, it is a gentle clean made for car paint. If you have a concern about a particular part of your car's finish, tell the captain before they start." },
      { q: "Do I Need A Water Tap For This Service?", a: "No. A Waterless Service needs no water connection — just a shaded or covered spot for the car." },
      { q: "Does The Waterless Service Include The Interior?", a: "Yes. Along with the waterless exterior clean, the captain vacuums the interior and polishes the dashboard." },
    ],
    related: ["star-wash", "jet-wash", "car-deep-cleaning"],
    image: { src: "/img/svc-waterless-960.webp", width: 960, height: 530 },
    vehicle: "car",
  },
  {
    slug: "car-deep-cleaning",
    apiSlug: "deep-cleaning",
    name: "Car Deep Cleaning",
    h1: "Car Deep Cleaning At Home In Indore",
    tagline: "Seats, floor, mats, pedals and door gates — a thorough interior clean plus a full foam wash.",
    intro: [
      "Deep Cleaning is our most thorough service: the interior cleaning many people go to a detailing studio for, done at your doorstep. On top of a foam wash, interior vacuum and dashboard polish, the captain cleans the seats, the floor and mats, the pedals and the door gates.",
      "It takes about an hour and a half per car. Book it every few months, after a long road trip, before selling a car, or whenever the cabin needs more than a quick vacuum.",
    ],
    includesFallback: ["Foam Wash, Vacuum & Dashboard Polish", "Seat Cleaning", "Floor & Mats Cleaning", "Pedal & Door (Gate) Cleaning"],
    minutesFallback: 90,
    priceFallback: 699,
    bestFor: ["Stained seats or muddy mats", "After a long road trip or monsoon season", "Getting a car ready to sell"],
    keepReady: KEEP_READY_WATER,
    faqs: [
      { q: "What Does Car Deep Cleaning Include?", a: "A foam wash, interior vacuum and dashboard polish, plus seat cleaning, floor and mats cleaning, and pedal and door (gate) cleaning." },
      { q: "How Long Does Deep Cleaning Take?", a: "About 90 minutes per car." },
      { q: "How Often Should I Get My Car Deep Cleaned?", a: "Every two to three months is enough for most cars, with a Star Wash or Jet Wash in between." },
    ],
    related: ["star-wash", "waterless-car-wash", "jet-wash"],
    image: { src: "/img/svc-deep-960.webp", width: 960, height: 542 },
    vehicle: "car",
  },
  {
    slug: "bike-wash",
    apiSlug: "bike-scooty-wash",
    variantSlugs: ["bike-wash-2-bikes"],
    name: "Bike Wash",
    h1: "Bike And Scooty Wash At Home In Indore",
    tagline: "A foam wash for your bike or scooty at your doorstep — book one or two together.",
    intro: [
      "Blussit washes two-wheelers at home too. The captain gives your bike or scooty a foam wash right where it is parked, so you don't have to ride it to a washing centre and wait.",
      "Booking two bikes together costs less than two separate washes, and a bike polish can be added for extra shine. You can also add a bike wash to a car wash in the same visit.",
    ],
    includesFallback: ["Bike Foam Wash"],
    minutesFallback: 20,
    priceFallback: 99,
    bestFor: ["Daily-use bikes and scooters", "Households with two or more two-wheelers", "Adding a bike to a car wash visit"],
    keepReady: KEEP_READY_WATER,
    faqs: [
      { q: "Do You Wash Scooters As Well As Bikes?", a: "Yes. Bike Wash covers motorcycles and scooters alike." },
      { q: "Can I Wash My Bike And Car In The Same Visit?", a: "Yes. Add an extra bike wash while booking a car wash and the captain does both in one visit." },
      { q: "How Long Does A Bike Wash Take?", a: "About 20 minutes for one bike." },
    ],
    related: ["jet-wash", "star-wash"],
    image: { src: "/img/svc-bike-960.webp", width: 960, height: 524 },
    vehicle: "bike",
  },
];

export const serviceBySlug = (slug: string | undefined) => SERVICES.find((s) => s.slug === slug);

export const HOW_IT_WORKS = [
  { title: "Book In A Minute", text: "Pick your car type and wash, then a date and time slot — online or on WhatsApp. No account needed, just your phone number." },
  { title: "A Captain Comes To You", text: "An ID-verified Blussit captain arrives at your home, office or society parking with everything needed." },
  { title: "Wash Done, Photos Saved", text: "The car is washed where it is parked. Before and after photos are saved on your booking. Pay in cash or online." },
];

export const INDORE_FAQS: Faq[] = [
  { q: "Do You Wash Cars At Home Anywhere In Indore?", a: "Yes. We come to any colony in Indore — your home, office or society parking. Drop a pin while booking; if a distance charge applies to your address, you see it before you confirm." },
  { q: "Is There A Car Wash Near Me That Comes To My Home?", a: "Yes — that is exactly what Blussit does. Instead of finding a car washing service centre near you, book a slot and a captain comes to your doorstep." },
  { q: "What Time Can I Book A Car Wash?", a: "Slots run from 7:00 AM to 7:00 PM, all 7 days. Pick any open slot while booking." },
  { q: "How Do I Pay?", a: "Pay in cash to the captain after the wash, or online by UPI, card or net banking. A few offer prices must be paid online when you book — the booking page tells you when." },
  { q: "Are Your Captains Verified?", a: "Every captain is ID-verified by our team before their first job, and takes before and after photos of every wash." },
];

export const PLANS_FAQS: Faq[] = [
  { q: "How Does The Monthly Pass Work?", a: "A Monthly Pass covers one car for one month with a set number of washes. The price depends on your car type and the wash you choose. Book each wash from your account whenever it suits you. Extras are paid separately, and unused washes don't carry over to the next month." },
  { q: "Do You Have Car Wash Plans For Housing Societies?", a: "Yes. With a Society Plan, a dedicated captain washes members' cars every morning, and each car also gets premium washes every month. Tap \"Request For Your Society\" and our team will call you." },
  { q: "I Have Many Cars Or A Fleet. Can You Help?", a: "Yes. Tap \"Request Custom Plan\", tell us how many vehicles you have and how often you want them washed, and we'll call you back with a price that fits." },
  { q: "How Do I Pay For A Plan?", a: "Monthly plans are paid online when you buy them." },
];
