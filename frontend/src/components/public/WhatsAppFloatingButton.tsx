

// Matches the public contact number displayed in the site footer. A Vite env
// value can replace it for a different production WhatsApp business account.
const DEFAULT_WHATSAPP_NUMBER = "918962288774";

export function openBlussitWhatsApp() {
  const phone = import.meta.env.VITE_WHATSAPP_NUMBER || DEFAULT_WHATSAPP_NUMBER;
  const message = encodeURIComponent("Hi Blussit, I would like to book a car wash.");
  window.open(`https://wa.me/${phone}?text=${message}`, "_blank", "noopener,noreferrer");
}

export function WhatsAppFloatingButton() {
  return (
    <button
      type="button"
      aria-label="Chat with Blussit on WhatsApp"
      onClick={() => openBlussitWhatsApp()}
      className="group z-[99999] flex h-[54px] sm:h-[58px] items-center gap-2 outline-none flex-row"
      style={{
        position: "fixed",
        right: "24px",
        bottom: "24px"
      }}
    >
      <span className="rounded-[10px] border border-white/[0.08] bg-[#181818] px-3.5 py-2.5 text-left leading-tight shadow-[0_8px_24px_rgba(0,0,0,0.30)] transition-shadow duration-200 group-hover:shadow-[0_10px_28px_rgba(0,0,0,0.38)] sm:px-3.5 sm:py-2.5">
        <span className="block text-[11px] font-medium text-white/60 sm:text-[12px]">Need help?</span>
        <span className="mt-0.5 block whitespace-nowrap text-[14px] font-bold text-white sm:text-[16px]">Chat with us</span>
      </span>

      <span className="flex h-[54px] w-[54px] shrink-0 items-center justify-center rounded-full border-2 border-white/90 bg-[#25D366] text-white shadow-[0_8px_24px_rgba(0,0,0,0.25)] transition-[transform,box-shadow] duration-200 sm:h-[58px] sm:w-[58px] group-hover:scale-[1.08] group-hover:shadow-[0_10px_28px_rgba(0,0,0,0.32)]">
        <svg viewBox="0 0 32 32" fill="currentColor" className="h-7 w-7" aria-hidden="true">
          <path d="M16.02 3.2a12.74 12.74 0 0 0-10.93 19.3L3.6 28.8l6.47-1.7a12.8 12.8 0 1 0 5.95-23.9Zm0 23.36a10.57 10.57 0 0 1-5.38-1.47l-.39-.23-3.84 1 1.03-3.74-.25-.4a10.58 10.58 0 1 1 8.83 4.84Zm5.8-7.91c-.32-.16-1.9-.94-2.19-1.05-.29-.1-.5-.16-.71.16-.2.32-.81 1.05-.99 1.27-.18.21-.36.24-.68.08a8.65 8.65 0 0 1-2.55-1.57 9.62 9.62 0 0 1-1.77-2.2c-.18-.32 0-.5.13-.66.14-.14.32-.37.47-.55.16-.19.21-.32.32-.53.1-.21.05-.4-.03-.56-.08-.16-.71-1.71-.97-2.34-.26-.61-.52-.53-.71-.54h-.61c-.21 0-.56.08-.85.4-.29.31-1.12 1.1-1.12 2.68 0 1.57 1.15 3.1 1.31 3.32.16.21 2.26 3.45 5.46 4.84.76.33 1.36.53 1.82.68.77.24 1.47.21 2.03.13.62-.09 1.9-.78 2.17-1.53.27-.75.27-1.4.19-1.53-.08-.13-.29-.21-.61-.37Z" />
        </svg>
      </span>
    </button>
  );
}

