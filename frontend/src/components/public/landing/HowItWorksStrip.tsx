import { ArrowRight, Check } from "lucide-react";
import { motion, useAnimationFrame, useInView, useMotionValue, useReducedMotion } from "framer-motion";
import { Link } from "react-router-dom";
import { useLayoutEffect, useRef, useState } from "react";

const NAVY = "#0E1A33";
const BLUE = "#0A66F0";
const MUTED = "#5F6878";

// Three steps joined by one winding blue dashed "road" — side by side on every screen.
const STEPS = [
  { num: "01", title: "Book Your Wash", text: "Pick the right service for your car." },
  { num: "02", title: "Choose A Time", text: "Select a date and time that suits you." },
  { num: "03", title: "We Come To You", text: "Our captain arrives at your doorstep." },
];

type StepState = "idle" | "active" | "done";
type Point = { x: number; y: number };

// One loop, one clock: the car's position AND every circle's state are read
// from the same elapsed time, so they can never drift apart.
const LOOP_MS = 6400;
const DRIVE_MS = 1900;
const LEG1_START = 700; // step 1 glows, then the car sets off
const LEG1_END = LEG1_START + DRIVE_MS; // arrives at step 2
const LEG2_START = LEG1_END + 700;
const LEG2_END = LEG2_START + DRIVE_MS; // arrives at step 3
const FINISH_TICK = LEG2_END + 500; // step 3 ticks, holds, loop restarts

const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

/** How far along the road (0 → 1) the car is at time `t`. */
function roadProgress(t: number): number {
  if (t < LEG1_START) return 0;
  if (t < LEG1_END) return ease((t - LEG1_START) / DRIVE_MS) * 0.5;
  if (t < LEG2_START) return 0.5;
  if (t < LEG2_END) return 0.5 + ease((t - LEG2_START) / DRIVE_MS) * 0.5;
  return 1;
}

function stepStates(t: number): StepState[] {
  return [
    t < LEG1_START ? "active" : "done",
    t < LEG1_END ? "idle" : t < LEG2_START ? "active" : "done",
    t < LEG2_END ? "idle" : t < FINISH_TICK ? "active" : "done",
  ];
}

/**
 * The road runs through the three circle centres, measured from the page —
 * so it lines up with the circles at every width. Between two circles it
 * rises then dips (a gentle S), and the halves join smoothly.
 */
function roadPath(pts: Point[], bend: number): string {
  let d = `M ${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length; i++) {
    const { x: x0 } = pts[i - 1];
    const { x: x1, y } = pts[i];
    const mid = (x0 + x1) / 2;
    const q = (x1 - x0) / 2;
    d += ` C ${x0 + 0.4 * q} ${y - bend}, ${mid - 0.4 * q} ${y - bend}, ${mid} ${y}`;
    d += ` C ${mid + 0.4 * q} ${y + bend}, ${x1 - 0.4 * q} ${y + bend}, ${x1} ${y}`;
  }
  return d;
}

export function HowItWorksStrip({ id = "how-it-works" }: { id?: string }) {
  const listRef = useRef<HTMLOListElement>(null);
  const circleRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const pathRef = useRef<SVGPathElement>(null);
  const inView = useInView(listRef, { margin: "-60px" });
  const reduceMotion = useReducedMotion();

  const [road, setRoad] = useState<{ w: number; h: number; d: string } | null>(null);
  const [states, setStates] = useState<StepState[]>(() => stepStates(0));

  const carX = useMotionValue(0);
  const carY = useMotionValue(0);
  const carRotate = useMotionValue(0);
  const elapsed = useRef(0);

  // Measure the circle centres (offsets ignore the steps' fade-in slide).
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const measure = () => {
      const pts = circleRefs.current.map((c) => {
        const li = c?.offsetParent as HTMLElement | null;
        if (!c || !li) return { x: 0, y: 0 };
        return { x: li.offsetLeft + c.offsetLeft + c.offsetWidth / 2, y: li.offsetTop + c.offsetTop + c.offsetHeight / 2 };
      });
      const bend = list.clientWidth < 768 ? 15 : 30;
      setRoad({ w: list.clientWidth, h: pts[0].y * 2, d: roadPath(pts, bend) });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(list);
    return () => ro.disconnect();
  }, []);

  useAnimationFrame((_, delta) => {
    const path = pathRef.current;
    if (reduceMotion || !inView || !path) return;
    // Off screen or in a background tab the loop simply pauses.
    elapsed.current = (elapsed.current + Math.min(delta, 100)) % LOOP_MS;
    const t = elapsed.current;

    const len = path.getTotalLength();
    const at = roadProgress(t) * len;
    const pt = path.getPointAtLength(at);
    const ahead = path.getPointAtLength(Math.min(at + 1, len));
    const behind = path.getPointAtLength(Math.max(at - 1, 0));
    carX.set(pt.x);
    carY.set(pt.y);
    carRotate.set((Math.atan2(ahead.y - behind.y, ahead.x - behind.x) * 180) / Math.PI);

    const next = stepStates(t);
    setStates((prev) => (prev.every((s, i) => s === next[i]) ? prev : next));
  });

  // Reduced motion: no car, every step shown plainly.
  const shown: StepState[] = reduceMotion ? ["active", "active", "active"] : states;

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
                  <path d="M2,8 C50,2 120,2 198,7" fill="none" stroke="#FFD21F" strokeWidth="3" strokeLinecap="round" />
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

        <ol ref={listRef} className="relative mt-8 grid grid-cols-3 gap-2 md:mt-10 md:gap-8">
          {road && (
            <svg
              width={road.w}
              height={road.h}
              viewBox={`0 0 ${road.w} ${road.h}`}
              className="pointer-events-none absolute left-0 top-0 overflow-visible"
              aria-hidden="true"
            >
              <path
                ref={pathRef}
                d={road.d}
                fill="none"
                stroke={BLUE}
                strokeWidth={2}
                strokeDasharray="6 6"
                strokeLinecap="round"
              />
            </svg>
          )}

          {/* The car drives under the circles, so it slips "into" each step.
              Its wheels sit on the road and it tilts around that contact point. */}
          {road && !reduceMotion && (
            <motion.div
              aria-hidden="true"
              className="pointer-events-none absolute left-0 top-0 z-[5]"
              style={{ x: carX, y: carY, rotate: carRotate, translateX: "-50%", translateY: "-92%", originX: 0.5, originY: 0.92 }}
            >
              <svg viewBox="0 0 36 18" className="h-[14px] w-[28px] drop-shadow-md md:h-[18px] md:w-[36px]">
                <path
                  d="M3,15 L3,10 C3,8.5 4,7 6,7 L12,7 C13.5,7 15,6 16,4.5 L20,4.5 C22.5,4.5 24,6 25.5,7.5 L29,7.5 C31,7.5 32.5,9 32.5,11 L32.5,14 C32.5,15.5 31,16.5 29,16.5 L6,16.5 C4,16.5 3,15.5 3,15 Z"
                  fill="#FFD21F"
                />
                <path d="M12.5,7 L15.5,4.5 C16,4 17,4 18,4 L23.5,4 C24.5,4 25.5,4.5 26.5,5.5 L28,7 L12.5,7 Z" fill={NAVY} opacity="0.9" />
                <circle cx="9" cy="16" r="3.5" fill={NAVY} />
                <circle cx="26" cy="16" r="3.5" fill={NAVY} />
                <circle cx="9" cy="16" r="1.5" fill="#F1F5F9" />
                <circle cx="26" cy="16" r="1.5" fill="#F1F5F9" />
              </svg>
            </motion.div>
          )}

          {STEPS.map((s, i) => {
            const state = shown[i];
            const lit = state !== "idle";
            return (
              <motion.li
                key={s.num}
                initial={{ opacity: 0, y: 14 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-40px" }}
                transition={{ duration: 0.4, delay: i * 0.12 }}
                className="relative z-10 flex flex-col items-center text-center"
              >
                <span
                  ref={(el) => {
                    circleRefs.current[i] = el;
                  }}
                  className={`relative flex h-11 w-11 items-center justify-center rounded-full text-[15px] font-extrabold ring-[6px] ring-white transition-[background-color,color,box-shadow,transform] duration-300 md:h-[60px] md:w-[60px] md:text-[19px] ${
                    lit ? "bg-[#0A66F0] text-white" : "bg-[#F1F5F9] text-[#5F6878]"
                  } ${state === "active" && !reduceMotion ? "scale-105 shadow-[0_0_0_12px_rgba(10,102,240,0.18)]" : ""}`}
                >
                  <span className={`absolute inset-0 flex items-center justify-center transition-all duration-300 ${state === "done" ? "scale-50 opacity-0" : "opacity-100"}`}>
                    {s.num}
                  </span>
                  <Check
                    className={`absolute h-5 w-5 text-white transition-all duration-300 md:h-6 md:w-6 ${state === "done" ? "scale-100 opacity-100" : "scale-50 opacity-0"}`}
                    strokeWidth={3}
                    aria-hidden="true"
                  />
                </span>
                <h3 className="mt-3 text-[14px] font-bold leading-tight md:mt-4 md:text-[19px]" style={{ color: NAVY }}>
                  {s.title}
                </h3>
                <p className="mt-1 max-w-[260px] text-[12px] leading-[1.4] md:mt-1.5 md:text-[15px]" style={{ color: MUTED }}>
                  {s.text}
                </p>
              </motion.li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
