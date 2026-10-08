import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";
import { PublicNavbar } from "../../../components/layout/PublicNavbar";
import { PublicFooter } from "../../../components/layout/PublicFooter";
import { MUTED, NAVY } from "./SeoKit";

/**
 * Unknown URLs used to bounce to "/" — to Google that's the homepage served
 * at every made-up address (a soft 404). Now they say so, stay out of the
 * index, and point somewhere useful.
 */
export default function NotFoundPage() {
  return (
    <div className="min-h-screen bg-white" style={{ color: NAVY }}>
      <Helmet>
        <title>Page Not Found | Blussit</title>
        <meta name="robots" content="noindex" />
      </Helmet>
      <PublicNavbar />
      <main className="container-page py-20 text-center md:py-28">
        <p className="font-mono-num text-[14px] font-extrabold tracking-[0.2em]" style={{ color: MUTED }}>
          404
        </p>
        <h1 className="mt-2 font-display text-[32px] font-extrabold sm:text-[40px]">Page Not Found</h1>
        <p className="mx-auto mt-3 max-w-md text-[15px] leading-[1.6]" style={{ color: MUTED }}>
          This page doesn't exist. You can book a doorstep wash, see our services, or go back to the home page.
        </p>
        <div className="mt-7 flex flex-wrap justify-center gap-3">
          <Link to="/book" className="inline-flex h-12 items-center rounded-full bg-[#FFD21F] px-7 text-[15px] font-bold" style={{ color: NAVY }}>
            Book A Wash
          </Link>
          <Link to="/services" className="inline-flex h-12 items-center rounded-full border border-[#D6DEEA] px-6 text-[15px] font-semibold">
            Our Services
          </Link>
          <Link to="/" className="inline-flex h-12 items-center rounded-full border border-[#D6DEEA] px-6 text-[15px] font-semibold">
            Home
          </Link>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}
