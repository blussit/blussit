import { useEffect, useState } from "react";

const LAUNCH_DATE = new Date("2026-09-14T00:00:00+05:30").getTime();

export function LaunchCountdownBar() {
  const [timeLeft, setTimeLeft] = useState({
    days: 0,
    hours: 0,
    minutes: 0,
    seconds: 0,
  });

  useEffect(() => {
    const timer = setInterval(() => {
      const now = new Date().getTime();
      const distance = LAUNCH_DATE - now;

      if (distance < 0) {
        clearInterval(timer);
        return;
      }

      setTimeLeft({
        days: Math.floor(distance / (1000 * 60 * 60 * 24)),
        hours: Math.floor(
          (distance % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60),
        ),
        minutes: Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60)),
        seconds: Math.floor((distance % (1000 * 60)) / 1000),
      });
    }, 1000);

    return () => clearInterval(timer);
  }, []);

  const pad = (num: number) => String(num).padStart(2, "0");

  return (
    <div className="w-full bg-[#FAFAFA] border-b border-gray-200 py-2 px-4 flex flex-col md:flex-row items-center justify-between text-xs sm:text-sm font-medium text-black">
      <div className="flex flex-col md:flex-row items-center gap-1 md:gap-4 text-center md:text-left mb-2 md:mb-0">
        <span className="font-bold tracking-wide">
          🚀 A SMARTER CAR-CARE EXPERIENCE IS COMING
        </span>
        <span className="hidden md:inline text-gray-300">|</span>
        <span className="text-gray-600 tracking-wide uppercase">
          LAUNCHING 14 SEPTEMBER 2026 &middot; INDORE
        </span>
      </div>

      <div className="flex items-center gap-3 font-mono-num font-bold tracking-wider">
        <div className="flex flex-col items-center">
          <span className="text-[#E8A900] text-base md:text-lg leading-none">
            {pad(timeLeft.days)}
          </span>
          <span className="text-[9px] uppercase text-gray-500 mt-0.5">
            DAYS
          </span>
        </div>
        <span className="text-gray-300 -mt-3">:</span>
        <div className="flex flex-col items-center">
          <span className="text-[#E8A900] text-base md:text-lg leading-none">
            {pad(timeLeft.hours)}
          </span>
          <span className="text-[9px] uppercase text-gray-500 mt-0.5">
            HOURS
          </span>
        </div>
        <span className="text-gray-300 -mt-3">:</span>
        <div className="flex flex-col items-center">
          <span className="text-[#E8A900] text-base md:text-lg leading-none">
            {pad(timeLeft.minutes)}
          </span>
          <span className="text-[9px] uppercase text-gray-500 mt-0.5">
            MINUTES
          </span>
        </div>
        <span className="text-gray-300 -mt-3">:</span>
        <div className="flex flex-col items-center">
          <span className="text-[#E8A900] text-base md:text-lg leading-none">
            {pad(timeLeft.seconds)}
          </span>
          <span className="text-[9px] uppercase text-gray-500 mt-0.5">
            SECONDS
          </span>
        </div>
      </div>
    </div>
  );
}
