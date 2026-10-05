import { ArrowRight, Check } from "lucide-react";
import { motion, useMotionValue, useAnimationFrame } from "framer-motion";
import { Link } from "react-router-dom";
import { useRef } from "react";

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

function easeInOut(t: number) {
  return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
}

export function HowItWorksStrip({ id = "how-it-works" }: { id?: string }) {
  const containerRef = useRef<HTMLOListElement>(null);
  const mobilePathRef = useRef<SVGPathElement>(null);
  const desktopPathRef = useRef<SVGPathElement>(null);

  const carX = useMotionValue(0);
  const carY = useMotionValue(0);
  const carRotate = useMotionValue(0);
  const carOpacity = useMotionValue(0);

  useAnimationFrame((time) => {
    const dur = 5600;
    const t = time % dur;
    
    let p = 0;
    let opacity = 1;
    
    if (t < 500) {
      p = 0;
      if (t < 150) opacity = t / 150;
    } else if (t < 2300) {
      const progressInInterval = (t - 500) / 1800;
      p = easeInOut(progressInInterval) * 0.5;
    } else if (t < 2800) {
      p = 0.5;
    } else if (t < 4600) {
      const progressInInterval = (t - 2800) / 1800;
      p = 0.5 + easeInOut(progressInInterval) * 0.5;
    } else {
      p = 1.0;
      if (t > 5450) opacity = 1 - (t - 5450) / 150;
    }

    const isMobile = window.innerWidth < 768;
    const path = isMobile ? mobilePathRef.current : desktopPathRef.current;
    const container = containerRef.current;
    
    if (path && container) {
      const len = path.getTotalLength();
      const pt = path.getPointAtLength(p * len);
      
      const ptNext = path.getPointAtLength(Math.min(p * len + 1, len));
      
      const containerW = container.getBoundingClientRect().width;
      const scaleX = containerW / 1000;
      const scaleY = 1; 
      
      const topOffset = isMobile ? -8 : 0;
      
      carX.set(pt.x * scaleX);
      carY.set(pt.y * scaleY + topOffset);
      
      const dx = (ptNext.x - pt.x) * scaleX;
      const dy = (ptNext.y - pt.y) * scaleY;
      let angle = Math.atan2(dy, dx) * (180 / Math.PI);
      
      if (p === 1.0) {
         const ptPrev = path.getPointAtLength(Math.max(len - 1, 0));
         const dxPrev = (pt.x - ptPrev.x) * scaleX;
         const dyPrev = (pt.y - ptPrev.y) * scaleY;
         angle = Math.atan2(dyPrev, dxPrev) * (180 / Math.PI);
      }
      
      carRotate.set(angle);
      carOpacity.set(opacity);
    }
  });

  return (
    <section id={id} className="bg-white py-8 md:py-12">
      <style>{`
        .step-circle { transition: all 0.3s ease; }
        .step-circle-1 { animation: step1-bg 5.6s infinite, step1-ring 5.6s infinite; }
        .step-num-1 { animation: step1-text 5.6s infinite; }
        .step-check-1 { animation: step1-check 5.6s infinite; }

        .step-circle-2 { animation: step2-bg 5.6s infinite, step2-ring 5.6s infinite; }
        .step-num-2 { animation: step2-text 5.6s infinite; }
        .step-check-2 { animation: step2-check 5.6s infinite; }

        .step-circle-3 { animation: step3-bg 5.6s infinite, step3-ring 5.6s infinite; }
        .step-num-3 { animation: step3-text 5.6s infinite; }
        .step-check-3 { animation: step3-check 5.6s infinite; }

        @keyframes step1-bg {
          0%, 100% { background-color: #0A66F0; color: white; }
        }
        @keyframes step1-ring {
          0%, 8.9% { box-shadow: 0 0 0 6px white, 0 0 0 14px rgba(10,102,240,0.2); transform: scale(1.05); }
          12%, 100% { box-shadow: 0 0 0 6px white, 0 0 0 6px rgba(10,102,240,0); transform: scale(1); }
        }
        @keyframes step1-text {
          0%, 8.9% { opacity: 1; transform: scale(1); }
          12%, 100% { opacity: 0; transform: scale(0.5); }
        }
        @keyframes step1-check {
          0%, 8.9% { opacity: 0; transform: scale(0.5); }
          12%, 100% { opacity: 1; transform: scale(1); }
        }

        @keyframes step2-bg {
          0%, 40% { background-color: #F1F5F9; color: #5F6878; }
          43%, 100% { background-color: #0A66F0; color: white; }
        }
        @keyframes step2-ring {
          0%, 40% { box-shadow: 0 0 0 6px white, 0 0 0 6px rgba(10,102,240,0); transform: scale(1); }
          43%, 50% { box-shadow: 0 0 0 6px white, 0 0 0 14px rgba(10,102,240,0.2); transform: scale(1.05); }
          53%, 100% { box-shadow: 0 0 0 6px white, 0 0 0 6px rgba(10,102,240,0); transform: scale(1); }
        }
        @keyframes step2-text {
          0%, 50% { opacity: 1; transform: scale(1); }
          53%, 100% { opacity: 0; transform: scale(0.5); }
        }
        @keyframes step2-check {
          0%, 50% { opacity: 0; transform: scale(0.5); }
          53%, 100% { opacity: 1; transform: scale(1); }
        }

        @keyframes step3-bg {
          0%, 81% { background-color: #F1F5F9; color: #5F6878; }
          84%, 100% { background-color: #0A66F0; color: white; }
        }
        @keyframes step3-ring {
          0%, 81% { box-shadow: 0 0 0 6px white, 0 0 0 6px rgba(10,102,240,0); transform: scale(1); }
          84%, 100% { box-shadow: 0 0 0 6px white, 0 0 0 14px rgba(10,102,240,0.2); transform: scale(1.05); }
        }
        @keyframes step3-text {
          0%, 100% { opacity: 1; transform: scale(1); }
        }
        @keyframes step3-check {
          0%, 100% { opacity: 0; transform: scale(0.5); }
        }
      `}</style>
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

        <ol ref={containerRef} className="relative mt-8 grid grid-cols-3 gap-2 md:mt-10 md:gap-8">
          <svg viewBox="0 0 1000 60" preserveAspectRatio="none" className="pointer-events-none absolute inset-x-0 top-[-8px] h-[60px] w-full md:hidden" aria-hidden="true">
            <path ref={mobilePathRef} d="M167 30 Q 333 2, 500 30 Q 667 58, 833 30" {...ROAD} />
          </svg>
          <svg viewBox="0 0 1000 60" preserveAspectRatio="none" className="pointer-events-none absolute inset-x-0 top-0 hidden h-[60px] w-full md:block" aria-hidden="true">
            <path ref={desktopPathRef} d="M167 30 C 250 -6, 300 -6, 333 30 S 417 66, 500 30 C 583 -6, 633 -6, 667 30 S 750 66, 833 30" {...ROAD} />
          </svg>
          
          <motion.div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              x: carX,
              y: carY,
              rotate: carRotate,
              opacity: carOpacity,
              pointerEvents: "none",
              zIndex: 20,
              translateX: "-50%",
              translateY: "-50%",
            }}
          >
            <div className="relative h-[18px] w-[36px]">
              <div className="absolute -left-2 top-1/2 h-[8px] w-[24px] -translate-y-1/2 rounded-full bg-[#0A66F0] opacity-40 blur-[3px]" />
              <svg viewBox="0 0 36 18" className="absolute inset-0 h-full w-full drop-shadow-md">
                <path
                  d="M3,15 L3,10 C3,8.5 4,7 6,7 L12,7 C13.5,7 15,6 16,4.5 L20,4.5 C22.5,4.5 24,6 25.5,7.5 L29,7.5 C31,7.5 32.5,9 32.5,11 L32.5,14 C32.5,15.5 31,16.5 29,16.5 L6,16.5 C4,16.5 3,15.5 3,15 Z"
                  fill="#FFD21F"
                />
                <path
                  d="M12.5,7 L15.5,4.5 C16,4 17,4 18,4 L23.5,4 C24.5,4 25.5,4.5 26.5,5.5 L28,7 L12.5,7 Z"
                  fill="#0E1A33"
                  opacity="0.9"
                />
                <circle cx="9" cy="16" r="3.5" fill="#0E1A33" />
                <circle cx="26" cy="16" r="3.5" fill="#0E1A33" />
                <circle cx="9" cy="16" r="1.5" fill="#F1F5F9" />
                <circle cx="26" cy="16" r="1.5" fill="#F1F5F9" />
              </svg>
            </div>
          </motion.div>

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
                className={`step-circle step-circle-${i + 1} relative z-10 flex h-11 w-11 items-center justify-center rounded-full text-[15px] font-extrabold ring-[6px] ring-white md:h-[60px] md:w-[60px] md:text-[19px]`}
                style={{ backgroundColor: i === 0 ? BLUE : "#F1F5F9", color: i === 0 ? "white" : MUTED }}
              >
                <span className={`step-num-${i + 1} absolute inset-0 flex items-center justify-center`}>{s.num}</span>
                <Check className={`step-check-${i + 1} absolute h-5 w-5 text-white md:h-6 md:w-6`} strokeWidth={3} />
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
