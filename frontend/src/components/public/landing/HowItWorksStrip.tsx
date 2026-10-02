import { useState } from "react";
import { Car, Clock, Store, ArrowRight } from "lucide-react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";

/**
 * IMAGES: public/img/ mein daalo (recommended 800x520, .webp)
 *  /img/how-01-book.webp, /img/how-02-time.webp, /img/how-03-arrive.webp
 */
const STEPS = [
  {
    num: "01",
    icon: Car,
    title: "Book your wash",
    text: "Choose the service that's right for your car.",
    image: "/card-1.png",
  },
  {
    num: "02",
    icon: Clock,
    title: "Choose a time",
    text: "Pick a convenient date and time slot.",
    image: "/card-2.png",
  },
  {
    num: "03",
    icon: Store,
    title: "We come to you",
    text: "Our professional captain arrives at your doorstep.",
    image: "/card-3.png",
  },
];

function StepImage({ src, alt, Icon }: { src: string; alt: string; Icon: typeof Car }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div
        className="flex h-full w-full items-center justify-center bg-gradient-to-br from-[#F4F8FF] to-[#DCEAFE]"
        role="img"
        aria-label={alt}
      >
        <Icon className="h-12 w-12 text-[#1677FF]/40" strokeWidth={1.5} />
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-full w-full object-cover"
    />
  );
}

export function HowItWorksStrip({ id = "how-it-works" }: { id?: string }) {
  return (
    <section id={id} className="bg-white">
      <div className="mx-auto w-full max-w-[1550px] px-5 pt-4 pb-4 md:pt-6 md:pb-6 lg:pt-8 lg:pb-8 sm:px-[45px]">
        {/* Heading - left top */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="max-w-[600px]">
            <span className="inline-block rounded-full bg-[#EEF4FF] px-3 py-1 text-[11px] font-semibold uppercase tracking-[1.2px] text-[#1677FF]">
              How it works
            </span>
            <h2 className="mt-3 font-display text-[26px] font-extrabold leading-[1.15] text-[#071A3D] sm:text-[30px] lg:text-[34px]">
              Car Care,{" "}
              <span className="relative inline-block text-[#1677FF]">
                Made Simple.
                <svg
                  viewBox="0 0 200 12"
                  preserveAspectRatio="none"
                  className="absolute -bottom-1.5 left-2 h-[8px] w-[75%]"
                  aria-hidden="true"
                >
                  <path
                    d="M2,8 C50,2 120,2 198,7"
                    fill="none"
                    stroke="#FACC15"
                    strokeWidth="3"
                    strokeLinecap="round"
                  />
                </svg>
              </span>
            </h2>
            <p className="mt-3 text-[14px] leading-[1.5] text-[#64748B] sm:text-[15px]">
              Book your wash, choose your time, and we'll come to your doorstep.
            </p>
          </div>

          <Link
            to="/book"
            className="inline-flex w-fit items-center rounded-full bg-[#FACC15] px-5 py-2.5 text-[14px] font-bold text-[#071A3D] shadow-[0_4px_14px_rgba(250,204,21,0.35)] transition hover:-translate-y-0.5"
          >
            Book Your Wash <ArrowRight className="ml-2 h-4 w-4" strokeWidth={2.5} />
          </Link>
        </div>

        {/* Cards */}
        <div className="hide-scrollbar -mx-5 mt-6 lg:mt-8 flex snap-x snap-mandatory gap-4 overflow-x-auto px-5 pb-4 pt-5 lg:mx-0 lg:grid lg:grid-cols-3 lg:gap-[22px] lg:overflow-visible lg:px-0 lg:pb-0">
          {STEPS.map((s, i) => (
            <motion.article
              key={s.num}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.4, delay: i * 0.1 }}
              className="relative w-[78vw] shrink-0 snap-center rounded-[20px] border border-[#E6EBF2] bg-white shadow-[0_6px_24px_rgba(15,30,60,0.06)] sm:w-[300px] lg:w-auto"
            >
              {/* Dashed connector to next card (desktop) */}
              <svg
                viewBox="0 0 100 48"
                preserveAspectRatio="none"
                className={`pointer-events-none absolute left-[36px] top-0 z-10 hidden h-[48px] lg:block ${
                  i < STEPS.length - 1 ? "w-[calc(100%-8px)]" : "w-[calc(100%-36px)]"
                }`}
                aria-hidden="true"
              >
                <path
                  d={
                    i < STEPS.length - 1
                      ? "M0 8 C25 -1, 60 22, 86 22 C93 22, 93 8, 100 8"
                      : "M0 8 C30 -1, 65 12, 100 22"
                  }
                  fill="none"
                  stroke="#1677FF"
                  strokeWidth="1.5"
                  strokeDasharray="5 5"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>

              {/* Number badge */}
              <div className="absolute -left-3 -top-4 z-20 flex h-[48px] w-[48px] items-center justify-center rounded-full border border-[#1677FF]/15 bg-[#F4F8FF] text-[16px] font-extrabold text-[#071A3D] shadow-sm">
                {s.num}
              </div>

              {/* Image */}
              <div className="h-[170px] w-full overflow-hidden rounded-t-[20px] sm:h-[190px] lg:h-[240px]">
                <StepImage src={s.image} alt={s.title} Icon={s.icon} />
              </div>

              {/* Content */}
              <div className="flex items-start gap-3 p-4 lg:min-h-[115px] lg:p-[20px]">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#EAF2FF]">
                  <s.icon className="h-5 w-5 text-[#0B2A6B]" strokeWidth={2} />
                </span>
                <div>
                  <h3 className="font-display text-[16px] font-bold leading-tight text-[#071A3D] sm:text-[17px]">
                    {s.title}
                  </h3>
                  <p className="mt-1 text-[13px] leading-[1.45] text-[#64748B] sm:text-[14px]">{s.text}</p>
                </div>
              </div>
            </motion.article>
          ))}
        </div>
      </div>
    </section>
  );
}