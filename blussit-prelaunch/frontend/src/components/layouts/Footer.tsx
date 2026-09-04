import { Instagram, MessageCircle } from "lucide-react";
import { siteConfig } from "@/config/site";

export function Footer() {
  return (
    <footer className="border-t border-[#E8DEC8] bg-[#FFFCF5]">
      <div className="mx-auto max-w-content px-6 py-7 md:px-10 md:py-8">
        {/* Main footer row */}
        <div className="flex flex-col gap-5 border-b border-[#E8DEC8] pb-6 sm:flex-row sm:items-center sm:justify-between">
          {/* Brand */}
          <div className="flex items-center gap-3">
            <img
              src="/blussit-logo.png"
              alt={siteConfig.brandName}
              className="h-6 w-auto object-contain"
            />

            <span className="hidden text-xs text-[#7D7467] sm:inline">
              Coming soon in{" "}
              <span className="font-semibold text-[#A87400]">
                {siteConfig.launchCity}
              </span>{" "}
              · {siteConfig.launchDateLabel}
            </span>
          </div>

          {/* Social + tagline */}
          <div className="flex flex-wrap items-center gap-4 sm:gap-5">
            <div className="flex items-center gap-3">
              <a
                href={siteConfig.social.instagram}
                target="_blank"
                rel="noreferrer"
                aria-label="Instagram"
                className="flex h-8 w-8 items-center justify-center rounded-full border border-[#DED3BD] text-[#6F675C] transition-all duration-200 hover:border-[#E8A900] hover:bg-[#FFF4CD] hover:text-[#A87400]"
              >
                <Instagram size={16} strokeWidth={1.8} />
              </a>

              <a
                href={siteConfig.social.whatsapp}
                target="_blank"
                rel="noreferrer"
                aria-label="WhatsApp"
                className="flex h-8 w-8 items-center justify-center rounded-full border border-[#DED3BD] text-[#6F675C] transition-all duration-200 hover:border-[#E8A900] hover:bg-[#FFF4CD] hover:text-[#A87400]"
              >
                <MessageCircle size={16} strokeWidth={1.8} />
              </a>
            </div>

            <span className="hidden h-4 w-px bg-[#DED3BD] sm:block" />

            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#514A40]">
              We make your car blush.
            </span>
          </div>
        </div>

        {/* Bottom row */}
        <div className="flex flex-col gap-2 pt-5 text-[11px] text-[#8A8174] sm:flex-row sm:items-center sm:justify-between">
          <p>
            © {new Date().getFullYear()} {siteConfig.brandName}. All rights
            reserved.
          </p>

          <div className="flex items-center gap-4">
            <a
              href="/privacy"
              className="transition-colors hover:text-[#A87400]"
            >
              Privacy
            </a>

            <a
              href="/terms"
              className="transition-colors hover:text-[#A87400]"
            >
              Terms
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}