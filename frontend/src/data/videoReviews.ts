/**
 * Customer video reviews shown on the landing page ("Watch what customers
 * say"). Paste one YouTube link per review — a normal link, a youtu.be link
 * or a Shorts link all work:
 *
 *   { url: "https://youtu.be/AbCdEfGhIjK", name: "Rahul S.", caption: "Star Wash, Vijay Nagar" }
 *
 * `name` and `caption` are optional. Until at least one link is added the
 * section stays hidden (nothing is shown that isn't a real review).
 */
export interface VideoReviewConfig {
  url: string;
  name?: string;
  caption?: string;
}

export const VIDEO_REVIEWS: VideoReviewConfig[] = [
  { url: "https://youtube.com/shorts/5VzN6nqD4l0" },
  { url: "https://youtube.com/shorts/qfccA7rUcDc" },
  { url: "https://youtube.com/shorts/-kO3kGo0ikg" },
];
