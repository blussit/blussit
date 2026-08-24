import { useEffect, useMemo, useState } from "react";

export interface CountdownParts {
  days: number;
  hours: number;
  minutes: number;
  seconds: number;
  isLaunched: boolean;
}

const SECOND = 1000;

/**
 * Calculates a live countdown to `targetISO`. `targetISO` should include
 * an explicit offset (e.g. "+05:30" for Asia/Kolkata) so the countdown is
 * correct regardless of the visitor's local timezone. Once the target
 * passes, isLaunched flips to true and every field clamps to 0 — it
 * never renders NaN, undefined, or negative numbers.
 */
export function useCountdown(targetISO: string): CountdownParts {
  const targetMs = useMemo(() => new Date(targetISO).getTime(), [targetISO]);
  const isValidTarget = Number.isFinite(targetMs);

  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!isValidTarget) return;
    const id = window.setInterval(() => setNow(Date.now()), SECOND);
    return () => window.clearInterval(id);
  }, [isValidTarget]);

  if (!isValidTarget) {
    return { days: 0, hours: 0, minutes: 0, seconds: 0, isLaunched: false };
  }

  const diff = targetMs - now;

  if (diff <= 0) {
    return { days: 0, hours: 0, minutes: 0, seconds: 0, isLaunched: true };
  }

  const totalSeconds = Math.floor(diff / SECOND);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  return { days, hours, minutes, seconds, isLaunched: false };
}
