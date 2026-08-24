let audioCtx: AudioContext | null = null;

/**
 * A short two-tone chime, synthesized via the Web Audio API — no external
 * sound asset to bundle or fetch. Browsers require a prior user gesture
 * (a click, a keypress) before audio is allowed to play; before that this
 * just silently no-ops rather than throwing, since a missed chime is never
 * worth breaking the rest of the notification flow over.
 */
export function playNotificationChime() {
  try {
    const AudioContextClass =
      window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextClass) return;
    if (!audioCtx) audioCtx = new AudioContextClass();
    if (audioCtx.state === "suspended") void audioCtx.resume();

    const ctx = audioCtx;
    const now = ctx.currentTime;
    [880, 1175].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = now + i * 0.12;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.15, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.25);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.3);
    });
  } catch {
    // Never let a sound failure break the rest of the notification flow.
  }
}
