import { ArrowRight } from "lucide-react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";

const NAVY = "#0E1A33";
const BLUE = "#0A66F0";
const MUTED = "#5F6878";

const ROAD = {
  fill: "none",
  stroke: BLUE,
  strokeWidth: 2,
  strokeDasharray: "6 6",
  strokeLinecap: "round" as const,
  vectorEffect: "non-scaling-stroke" as const,
};

// Three steps joined by one winding blue dashed "road" — side by side on every screen.
const STEPS = [
  { num: "01", title: "Book Your Wash", text: "Pick the right service for your car." },
  { num: "02", title: "Choose A Time", text: "Select a date and time that suits you." },
  { num: "03", title: "We Come To You", text: "Our captain arrives at your doorstep." },
];

export function HowItWorksStrip({ id = "how-it-works" }: { id?: string }) {
  return (
    <section id={id} className="bg-white py-8 md:py-12">
      <div className="container-page">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="max-w-[600px]">
            <p className="text-[14px] font-extrabold uppercase tracking-[0.1em] md:text-[15px]" style={{ color: BLUE }}>
              How it works
            </p>
            <h2 className="mt-2 font-display text-[26px] font-extrabold leading-[1.15] sm:text-[30px] lg:text-[34px]" style={{ color: NAVY }}>
              Car Care,{" "}
              <span className="relative inline-block" style={{ color: BLUE }}>
                Made Simple.
                <svg viewBox="0 0 200 12" preserveAspectRatio="none" className="absolute -bottom-1.5 left-2 h-[8px] w-[75%]" aria-hidden="true">
                  <path d="M2,8 C50,2 120,2 198,7" fill="none" stroke="#FACC15" strokeWidth="3" strokeLinecap="round" />
                </svg>
              </span>
            </h2>
            <p className="mt-3 text-[14px] leading-[1.5] sm:text-[15px]" style={{ color: MUTED }}>
              A spotless car in three easy steps — no driving, no waiting.
            </p>
          </div>
          <Link
            to="/book"
            className="hidden w-fit items-center rounded-full bg-[#FFD21F] px-5 py-2.5 text-[14px] font-bold shadow-[0_6px_16px_rgba(255,200,0,0.32)] transition hover:-translate-y-0.5 sm:inline-flex"
            style={{ color: NAVY }}
          >
            Book Your Wash <ArrowRight className="ml-2 h-4 w-4" strokeWidth={2.5} />
          </Link>
        </div>

        <ol className="relative mt-8 grid grid-cols-3 gap-2 md:mt-10 md:gap-8">
          {/* A winding dashed "road" from the first circle to the last: it
              leaves each circle heading up and arrives at the next from below. */}
          {/* Phones: one gentle arc per gap (up, then down) — the gaps are short. */}
          <svg viewBox="0 0 1000 60" preserveAspectRatio="none" className="pointer-events-none absolute inset-x-0 top-[-8px] h-[60px] w-full md:hidden" aria-hidden="true">
            <path d="M167 30 Q 333 2, 500 30 Q 667 58, 833 30" {...ROAD} />
          </svg>
          <svg viewBox="0 0 1000 60" preserveAspectRatio="none" className="pointer-events-none absolute inset-x-0 top-0 hidden h-[60px] w-full md:block" aria-hidden="true">
            <path d="M167 30 C 250 -6, 300 -6, 333 30 S 417 66, 500 30 C 583 -6, 633 -6, 667 30 S 750 66, 833 30" {...ROAD} />
          </svg>
          {STEPS.map((s, i) => (
            <motion.li
              key={s.num}
              initial={{ opacity: 0, y: 14 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-40px" }}
              transition={{ duration: 0.4, delay: i * 0.12 }}
              className="relative flex flex-col items-center text-center"
            >
              <span
                className="relative z-10 flex h-11 w-11 items-center justify-center rounded-full text-[15px] font-extrabold text-white ring-[6px] ring-white md:h-[60px] md:w-[60px] md:text-[19px]"
                style={{ backgroundColor: BLUE }}
              >
                {s.num}
              </span>
              <h3 className="mt-3 text-[14px] font-bold leading-tight md:mt-4 md:text-[19px]" style={{ color: NAVY }}>
                {s.title}
              </h3>
              <p className="mt-1 max-w-[260px] text-[12px] leading-[1.4] md:mt-1.5 md:text-[15px]" style={{ color: MUTED }}>
                {s.text}
              </p>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  );
}
