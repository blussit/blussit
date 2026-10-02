import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Play, ChevronLeft, ChevronRight } from "lucide-react";
import { VIDEO_REVIEWS, type VideoReviewConfig } from "../../../data/videoReviews";

interface ParsedVideo {
  id: string;
  isShort: boolean;
}

function parseYouTube(input: string): { id: string; isShort: boolean } | null {
  const text = input.trim();
  if (/^[\w-]{11}$/.test(text)) return { id: text, isShort: false };

  try {
    const url = new URL(text);
    const host = url.hostname.replace(/^www\.|^m\./, "");
    let id: string | null = null;
    let isShort = false;

    if (host === "youtu.be") {
      id = url.pathname.slice(1).split("/")[0];
    } else if (
      host.endsWith("youtube.com") ||
      host.endsWith("youtube-nocookie.com")
    ) {
      const parts = url.pathname.split("/").filter(Boolean);

      if (parts[0] === "shorts") {
        id = parts[1];
        isShort = true;
      } else if (parts[0] === "embed" || parts[0] === "live") {
        id = parts[1];
      } else {
        id = url.searchParams.get("v");
      }
    }

    return id && /^[\w-]{11}$/.test(id) ? { id, isShort } : null;
  } catch {
    return null;
  }
}

function toParsed(item: VideoReviewConfig): ParsedVideo | null {
  const parsed = parseYouTube(item.url);
  return parsed ? { id: parsed.id, isShort: parsed.isShort } : null;
}

export function VideoReviews({ id = "video-reviews" }: { id?: string }) {
  const videos = useMemo(
    () => VIDEO_REVIEWS.map(toParsed).filter(Boolean) as ParsedVideo[],
    []
  );

  const scrollerRef = useRef<HTMLDivElement>(null);
  const interactTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [isInteracting, setIsInteracting] = useState(false);
  const [activeHover, setActiveHover] = useState<number | null>(null);
  const [playingVideoIndex, setPlayingVideoIndex] = useState<number | null>(null);

  const MULTIPLIER = 6;

  const allVideos = useMemo(
    () => Array.from({ length: MULTIPLIER }).flatMap(() => videos),
    [videos]
  );

  const handleInteract = () => {
    setIsInteracting(true);
  };

  const handleInteractEnd = () => {
    if (interactTimeout.current) clearTimeout(interactTimeout.current);

    interactTimeout.current = setTimeout(() => {
      setIsInteracting(false);
    }, 1800);
  };

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;

    const onUserScroll = () => {
      handleInteract();
      handleInteractEnd();
    };

    el.addEventListener("wheel", onUserScroll, { passive: true });
    el.addEventListener("touchstart", onUserScroll, { passive: true });
    el.addEventListener("touchmove", onUserScroll, { passive: true });

    return () => {
      el.removeEventListener("wheel", onUserScroll);
      el.removeEventListener("touchstart", onUserScroll);
      el.removeEventListener("touchmove", onUserScroll);
    };
  }, []);

  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el || !allVideos.length) return;

    const setWidth = el.scrollWidth / MULTIPLIER;
    el.scrollLeft = setWidth * 2;
  }, [allVideos.length]);

  // Continuous train-like infinite carousel.
  // It keeps moving even when the pointer is over a card.
  useEffect(() => {
    let animationId: number;
    let lastTime = performance.now();

    const step = (time: number) => {
      const dt = time - lastTime;
      lastTime = time;

      const el = scrollerRef.current;

      if (el && !isInteracting && playingVideoIndex === null) {
        // Slow, premium continuous movement.
        el.scrollLeft += dt * 0.028;

        const setWidth = el.scrollWidth / MULTIPLIER;

        if (el.scrollLeft >= setWidth * (MULTIPLIER - 2)) {
          el.scrollLeft -= setWidth;
        } else if (el.scrollLeft <= setWidth) {
          el.scrollLeft += setWidth;
        }
      }

      animationId = requestAnimationFrame(step);
    };

    animationId = requestAnimationFrame(step);

    return () => cancelAnimationFrame(animationId);
  }, [isInteracting, playingVideoIndex]);

  const scrollManual = (dir: 1 | -1) => {
    handleInteract();
    handleInteractEnd();

    const el = scrollerRef.current;
    if (!el) return;

    const first = el.firstElementChild as HTMLElement | null;
    const step = first ? first.offsetWidth + 18 : 260;

    el.scrollBy({
      left: dir * step * 2,
      behavior: "smooth",
    });
  };

  if (!videos.length) return null;

  return (
    <>
      <section id={id} className="bg-white">
        <div className="mx-auto w-full max-w-[1550px] px-5 pt-4 pb-4 md:pt-6 md:pb-6 lg:pt-8 lg:pb-8 sm:px-[45px]">
          {/* Heading */}
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="max-w-[650px]">
              <span className="inline-block rounded-full bg-[#EEF4FF] px-3 py-1 text-[11px] font-semibold uppercase tracking-[1.2px] text-[#1677FF]">
                CUSTOMER REVIEWS
              </span>

              <h2 className="mt-3 font-display text-[26px] font-extrabold leading-[1.15] text-[#071A3D] sm:text-[30px] lg:text-[34px]">
                Real People,{" "}
                <span className="relative inline-block text-[#1677FF]">
                  Real Clean Cars.
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
                Real customers, real doorstep washes — straight from them.
              </p>
            </div>

            {/* Navigation is placed on the left/right edges of the cards below. */}
          </div>

          {/* Reviews carousel */}
          <div className="relative mt-6 lg:mt-8">
            {/* Arrows sit in the white gutters beside the cards — never on top of a video. */}
            <button
              type="button"
              onClick={() => scrollManual(-1)}
              className="absolute left-[-2px] top-1/2 z-30 hidden h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-[#E6EBF2] bg-white text-[#071A3D] shadow-[0_4px_14px_rgba(15,30,60,0.10)] transition-all hover:scale-105 hover:border-[#1677FF] hover:text-[#1677FF] sm:flex lg:left-[-18px] xl:left-[-28px]"
              aria-label="Previous videos"
            >
              <ChevronLeft className="h-5 w-5" />
            </button>

            <button
              type="button"
              onClick={() => scrollManual(1)}
              className="absolute right-[-2px] top-1/2 z-30 hidden h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full border border-[#E6EBF2] bg-white text-[#071A3D] shadow-[0_4px_14px_rgba(15,30,60,0.10)] transition-all hover:scale-105 hover:border-[#1677FF] hover:text-[#1677FF] sm:flex lg:right-[-18px] xl:right-[-28px]"
              aria-label="Next videos"
            >
              <ChevronRight className="h-5 w-5" />
            </button>

            <div
              ref={scrollerRef}
              className="flex items-center gap-4 overflow-x-auto px-3 py-5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:gap-[18px] sm:px-5"
              onMouseDown={handleInteract}
              onMouseUp={handleInteractEnd}
              onTouchStart={handleInteract}
              onTouchEnd={handleInteractEnd}
            >
              {allVideos.map((video, index) => {
                const isHovered = activeHover === index;

                return (
                  <div
                    key={`${video.id}-${index}`}
                    className="w-[230px] shrink-0 sm:w-[235px] md:w-[245px] lg:w-[calc(20%-14.4px)]"
                    onMouseEnter={() => setActiveHover(index)}
                    onMouseLeave={() => setActiveHover(null)}
                  >
                    <VideoCard
                      video={video}
                      highlighted={isHovered}
                      playing={playingVideoIndex === index}
                      onPlay={() => {
                        setPlayingVideoIndex((current) =>
                          current === index ? null : index
                        );
                        handleInteract();
                        handleInteractEnd();
                      }}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </section>

    </>
  );
}

function VideoCard({
  video,
  highlighted,
  playing,
  onPlay,
}: {
  video: ParsedVideo;
  highlighted: boolean;
  playing: boolean;
  onPlay: () => void;
}) {
  return (
    <div
      role={playing ? undefined : "button"}
      tabIndex={playing ? undefined : 0}
      onClick={playing ? undefined : onPlay}
      onKeyDown={
        playing
          ? undefined
          : (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onPlay();
              }
            }
      }
      aria-label={playing ? undefined : "Watch customer review"}
      className={[
        "group relative block w-full overflow-hidden rounded-[20px] bg-[#071A3D]",
        "aspect-[3/4]",
        "border transition-all duration-500 ease-out",
        highlighted
          ? "z-20 -translate-y-2 scale-[1.035] border-[#1677FF] shadow-[0_18px_45px_rgba(22,119,255,0.20)]"
          : "border-[#E5EAF2] shadow-[0_8px_24px_rgba(15,30,60,0.08)]",
      ].join(" ")}
    >
      {playing ? (
        <iframe
          src={`https://www.youtube-nocookie.com/embed/${video.id}?autoplay=1&rel=0&modestbranding=1&playsinline=1`}
          title="Customer review"
          className="absolute inset-0 h-full w-full border-0"
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          allowFullScreen
          referrerPolicy="strict-origin-when-cross-origin"
        />
      ) : (
        <>
          <img
            src={`https://i.ytimg.com/vi/${video.id}/maxresdefault.jpg`}
            onError={(event) => {
              const image = event.currentTarget;
              if (!image.src.includes("hqdefault.jpg")) {
                image.src = `https://i.ytimg.com/vi/${video.id}/hqdefault.jpg`;
              }
            }}
            alt="Customer review"
            loading="lazy"
            className="absolute inset-0 h-full w-full object-cover object-center scale-[1.52] transition-transform duration-700 ease-out group-hover:scale-[1.58]"
          />

          <span
            className={[
              "absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2",
              "h-14 w-14 items-center justify-center rounded-full",
              "bg-white/95 text-[#1677FF] shadow-[0_8px_30px_rgba(0,0,0,0.18)]",
              "backdrop-blur-sm transition-all duration-300",
              highlighted ? "scale-110" : "group-hover:scale-110",
            ].join(" ")}
          >
            <Play className="ml-0.5 h-6 w-6 fill-[#1677FF]" />
          </span>
        </>
      )}

      {/* Small bottom accent — no names, stars or extra text */}
      <span className="absolute bottom-0 left-0 right-0 h-[4px] bg-gradient-to-r from-[#1677FF] via-[#FACC15] to-[#1677FF] opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
    </div>
  );
}
