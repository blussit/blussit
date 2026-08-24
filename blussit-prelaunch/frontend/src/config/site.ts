// Central, editable configuration for launch + brand copy.
// Keeping this separate means the launch date, city, or headline
// can change without touching component code.

export const siteConfig = {
  brandName: "BLUSSIT",
  tagline: "We make your car blush.",

  launchCity: "Indore",
  // ISO string, interpreted in Asia/Kolkata (IST, UTC+5:30)
  launchDateISO: "2026-09-14T10:00:00+05:30",
  launchDateLabel: "14 September 2026",
  launchTimeLabel: "10:00 AM",

  heroKicker: "Premium car care is arriving",
  heroTitleLines: ["INDORE,", "GET READY", "TO"],
  heroTitleGoldWord: "BLUSH.",
  heroDescription:
    "A smarter car-care experience is coming to your doorstep. Premium cleaning and care, right where your car is parked.",

  ctaPrimaryPreLaunch: "Get Early Access",
  ctaPrimaryPostLaunch: "Book a Service",
  ctaSecondary: "Stay Tuned",
  formCta: "Notify Me When We Launch",

  social: {
    instagram: "#",
    whatsapp: "#",
  },
} as const;

export type SiteConfig = typeof siteConfig;
