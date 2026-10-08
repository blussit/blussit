/**
 * Landing page FAQ. Edit the text here — it ships with the website, so a
 * change goes live on the next deploy (no database step). Keep answers short,
 * plain and true to how bookings, plans and payments actually work.
 */
export interface Faq {
  id: string;
  question: string;
  answer: string;
}

export const FAQS: Faq[] = [
  {
    id: "how-it-works",
    question: "How Does Blussit Work?",
    answer:
      "Book online or on WhatsApp in about a minute: choose your car type and wash, then pick a date and time. A trained Blussit captain comes to your home, office or society parking and washes the car right where it is parked. You don't drive anywhere or wait in a queue.",
  },
  {
    id: "areas",
    question: "Which Areas Do You Serve?",
    answer:
      "Every colony in Indore. Drop a pin while booking and a captain comes to that spot. If a distance charge applies to your address, you see it before you confirm.",
  },
  {
    id: "keep-ready",
    question: "What Do I Need To Keep Ready?",
    answer:
      "Park the car where it can be washed safely. For Jet Wash, Foam Wash, Star Wash, Deep Cleaning and Bike Wash, please keep water and electricity available. A Waterless Wash needs no water connection — just a shaded or covered spot.",
  },
  {
    id: "payment",
    question: "How Do I Pay?",
    answer:
      "Pay in cash to the captain after the wash, or online by UPI, card or net banking. A few offer prices must be paid online when you book — the booking page tells you when. Monthly plans are paid online.",
  },
  {
    id: "cancel",
    question: "Can I Cancel Or Change My Booking?",
    answer:
      "Yes. You can change the date or time from your account until the captain is on the way. Cancelling is free up to 4 hours before your slot. After that, a small amount is deducted (₹50 to ₹100) — see our Cancellation Policy.",
  },
  {
    id: "monthly-pass",
    question: "How Does The Monthly Pass Work?",
    answer:
      "A Monthly Pass covers one car for one month with a set number of washes. The price depends on your car type and the wash you choose. Book each wash from your account whenever it suits you. Extras like polish are paid separately, and unused washes don't carry over to the next month.",
  },
  {
    id: "society",
    question: "Do You Have Plans For Housing Societies?",
    answer:
      "Yes. With a Society Plan, a dedicated captain washes members' cars every morning, and each car also gets premium washes every month. Tap \"Request For Your Society\" in the plans section, share your society details, and our team will call you.",
  },
  {
    id: "safety",
    question: "Is My Car Safe With Your Captains?",
    answer:
      "Every captain is ID-verified by our team before their first job. The captain takes photos before and after every wash, and these are saved on your booking. If anything isn't right, raise a request from your booking and our team will check it with those photos.",
  },
  {
    id: "custom",
    question: "I Have Many Cars Or A Fleet. Can You Help?",
    answer:
      "Yes. Tap \"Request Custom Plan\", tell us how many vehicles you have and how often you want them washed, and we'll call you back with a price that fits.",
  },
];
