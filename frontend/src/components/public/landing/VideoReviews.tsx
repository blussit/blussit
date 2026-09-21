import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Play, Volume2 } from "lucide-react";
import { SectionHeader, SectionShell } from "./shared";
import { VIDEO_REVIEWS, type VideoReviewConfig } from "../../../data/videoReviews";

interface ParsedVideo {
  id: string;
  isShort: boolean;
  name?: string;
  caption?: string;
}

/** A YouTube link (watch / youtu.be / shorts / embed) or a bare 11-character id. */
function parseYouTube(input: string): { id: string; isShort: boolean } | null {
  const text = input.trim();
  if (/^[\w-]{11}$/.test(text)) return { id: text, isShort: false };
  try {
    const url = new URL(text);
    const host = url.hostname.replace(/^www\.|^m\./, "");
    let id: string | null = null;
    let isShort = false;
    if (host === "youtu.be") id = url.pathname.slice(1).split("/")[0];
    else if (host.endsWith("youtube.com") || host.endsWith("youtube-nocookie.com")) {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts[0] === "shorts") {
        id = parts[1];
        isShort = true;
      } else if (parts[0] === "embed" || parts[0] === "live") id = parts[1];
      else id = url.searchParams.get("v");
    }
    return id && /^[\w-]{11}$/.test(id) ? { id, isShort } : null;
  } catch {
    return null;
  }
}

function toParsed(item: VideoReviewConfig): ParsedVideo | null {
  const parsed = parseYouTube(item.url);
  return parsed ? { ...parsed, name: item.name, caption: item.caption } : null;
}

/**
 * Customer video reviews as a one-video-at-a-time carousel: the video in the
 * middle of the frame is the main one and the ONLY one that plays (muted —
 * browsers only allow silent autoplay); its neighbours wait as thumbnails.
 * Swipe (or tap a neighbour / a dot) to change which one is the main video.
 * Nothing plays while the section is off-screen, and visitors who prefer
 * reduced motion get tap-to-play. "Sound on" plays with sound and the choice
 * carries over as they swipe on.
 */
export function VideoReviews({ id = "video-reviews" }: { id?: string }) {
  const videos = useMemo(() => VIDEO_REVIEWS.map(toParsed).filter(Boolean) as ParsedVideo[], []);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const frame = useRef(0);
  const [active, setActive] = useState(() => Math.floor((videos.length - 1) / 2));
  // visible = any part of the row on screen; ready = mostly on screen (autoplay
  // starts). Playback continues until it is fully off-screen, so a row that is
  // only partly in view doesn't flicker back to thumbnails.
  const [visible, setVisible] = useState(false);
  const [ready, setReady] = useState(false);
  const [sound, setSound] = useState(false);
  const reduced = useMemo(() => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches, []);

  const centreOn = useCallback((index: number, smooth: boolean) => {
    const el = scrollerRef.current;
    const card = el?.children[index] as HTMLElement | undefined;
    if (!el || !card) return;
    el.scrollTo({ left: card.offsetLeft + card.offsetWidth / 2 - el.clientWidth / 2, behavior: smooth ? "smooth" : "auto" });
  }, []);

  // Open on the middle video, with a neighbour peeking on each side.
  useLayoutEffect(() => {
    centreOn(Math.floor((videos.length - 1) / 2), false);
  }, [centreOn, videos.length]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      ([entry]) => {
        setVisible(entry.isIntersecting);
        if (!entry.isIntersecting) {
          setReady(false);
          setSound(false); // sound never resumes unasked after scrolling away
        } else if (entry.intersectionRatio >= 0.5) setReady(true);
      },
      { threshold: [0, 0.5] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  // Whichever card sits closest to the middle of the row is the main video.
  const onScroll = () => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const el = scrollerRef.current;
      if (!el) return;
      const mid = el.scrollLeft + el.clientWidth / 2;
      let best = 0;
      let bestDistance = Number.POSITIVE_INFINITY;
      Array.from(el.children).forEach((child, index) => {
        const card = child as HTMLElement;
        const distance = Math.abs(card.offsetLeft + card.offsetWidth / 2 - mid);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = index;
        }
      });
      setActive(best);
    });
  };

  if (!videos.length) return null;

  const select = (index: number) => {
    setActive(index);
    centreOn(index, true);
  };

  return (
    <SectionShell id={id} className="border-t border-cream-line-soft bg-white">
      <SectionHeader title="Watch what customers say" subtitle="Real customers, real doorstep washes — straight from them." />
      <div className="-mx-4 mt-8 sm:mt-10 md:mx-0">
        <div
          ref={scrollerRef}
          onScroll={onScroll}
          className="relative flex snap-x snap-mandatory items-start gap-3 overflow-x-auto overscroll-x-contain px-[calc(50%_-_min(32vw,135px))] pb-2 [scrollbar-width:none] md:justify-center-safe md:gap-6 md:px-4 [&::-webkit-scrollbar]:hidden"
        >
          {videos.map((video, index) => (
            <VideoCard
              key={video.id}
              video={video}
              main={index === active}
              playing={index === active && visible && (sound || (ready && !reduced))}
              withSound={index === active && sound}
              onSelect={() => select(index)}
              onSound={() => setSound(true)}
            />
          ))}
        </div>
        {videos.length > 1 && <p className="mt-3 text-center text-xs font-medium text-neutral-500 md:hidden">Swipe to watch more</p>}
        {videos.length > 1 && (
          <div className="mt-3 flex justify-center gap-2">
            {videos.map((video, index) => (
              <button
                key={video.id}
                type="button"
                onClick={() => select(index)}
                aria-label={`Show video ${index + 1} of ${videos.length}`}
                aria-current={index === active}
                className={`h-2 rounded-full transition-all ${index === active ? "w-6 bg-black" : "w-2 bg-[#E8D9A6] hover:bg-[#E8A900]"}`}
              />
            ))}
          </div>
        )}
      </div>
    </SectionShell>
  );
}

function VideoCard({
  video,
  main,
  playing,
  withSound,
  onSelect,
  onSound,
}: {
  video: ParsedVideo;
  main: boolean;
  playing: boolean;
  withSound: boolean;
  onSelect: () => void;
  onSound: () => void;
}) {
  const src =
    `https://www.youtube-nocookie.com/embed/${video.id}?autoplay=1&mute=${withSound ? 0 : 1}&loop=1&playlist=${video.id}` +
    `&playsinline=1&rel=0&modestbranding=1&controls=1`;

  return (
    <figure
      className={`w-[min(64vw,270px)] shrink-0 snap-center snap-always transition-all duration-300 md:w-[280px] ${
        main ? "scale-100 opacity-100" : "scale-[0.92] opacity-60 hover:opacity-90"
      }`}
    >
      <div className={`relative overflow-hidden rounded-2xl border border-cream-line bg-black ${video.isShort ? "aspect-[9/16]" : "aspect-video"}`}>
        {playing ? (
          <iframe
            key={withSound ? "sound" : "muted"}
            src={src}
            title={video.name ? `Review by ${video.name}` : "Customer review"}
            className="absolute inset-0 h-full w-full"
            allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
          />
        ) : (
          <button
            type="button"
            onClick={main ? onSound : onSelect}
            className="group absolute inset-0 h-full w-full"
            aria-label={video.name ? `Play review by ${video.name}` : "Play customer review"}
          >
            <img
              src={`https://i.ytimg.com/vi/${video.id}/hqdefault.jpg`}
              alt=""
              loading="lazy"
              className="h-full w-full object-cover opacity-90 transition-opacity group-hover:opacity-100"
            />
            <span className="absolute inset-0 flex items-center justify-center">
              <span className="flex h-14 w-14 items-center justify-center rounded-full bg-white text-black shadow-lg transition-transform group-hover:scale-105">
                <Play className="ml-0.5 h-6 w-6 fill-black" />
              </span>
            </span>
          </button>
        )}
        {playing && !withSound && (
          // A player iframe swallows touches, so a swipe that starts on the
          // main video would never reach the carousel. While it plays muted,
          // this clear layer sits on top: a swipe scrolls the row, a tap
          // turns the sound on (after which the player's own controls are free).
          <button type="button" onClick={onSound} tabIndex={-1} aria-hidden="true" className="absolute inset-0 z-10 cursor-pointer" />
        )}
        {playing && !withSound && (
          <button
            type="button"
            onClick={onSound}
            className="absolute bottom-3 left-3 z-20 inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-xs font-semibold text-black shadow-md ring-1 ring-black/10 hover:bg-[#FFF4CD]"
          >
            <Volume2 className="h-3.5 w-3.5" /> Sound on
          </button>
        )}
      </div>
      {(video.name || video.caption) && (
        <figcaption className="mt-3 px-1">
          {video.name && <p className="text-[14px] font-semibold text-black">{video.name}</p>}
          {video.caption && <p className="text-[13px] text-neutral-600">{video.caption}</p>}
        </figcaption>
      )}
    </figure>
  );
}
