import { Instagram, MessageCircle } from "lucide-react";
import { siteConfig } from "@/config/site";

export function Footer() {
  return (
    <footer className="bg-cream py-10">
      <div className="mx-auto max-w-content px-6 md:px-10">
        <div className="flex flex-col gap-6 border-t border-border pt-8 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <img src="/blussit-logo.png" alt={siteConfig.brandName} className="h-5 w-auto" />
            <span className="hidden text-xs text-muted sm:inline">
              Coming soon in <span className="font-semibold text-goldDeep">{siteConfig.launchCity}</span>{" "}
              · {siteConfig.launchDateLabel}
            </span>
          </div>

          <div className="flex items-center gap-5 text-sm text-muted">
            <a
              href={siteConfig.social.instagram}
              aria-label="Instagram"
              className="hover:text-goldDeep"
            >
              <Instagram size={17} />
            </a>
            <a
              href={siteConfig.social.whatsapp}
              aria-label="WhatsApp"
              className="hover:text-goldDeep"
            >
              <MessageCircle size={17} />
            </a>
            <span className="text-xs font-bold uppercase tracking-wide text-ink/60">
              We make your car blush.
            </span>
          </div>
        </div>

        <p className="mt-6 text-xs text-muted/70">
          © {new Date().getFullYear()} {siteConfig.brandName}. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
