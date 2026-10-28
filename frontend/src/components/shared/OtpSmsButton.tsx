/**
 * "Send Code By SMS Instead" — in every OTP popup, from the first send on.
 * Asks the backend for the SAME live code by SMS (so the popup's one input
 * accepts it, no page change); disabled with a countdown while the server's
 * per-phone cooldown runs.
 */
export function OtpSmsButton({
  cooldown,
  disabled,
  onClick,
  className = "",
}: {
  cooldown: number;
  disabled?: boolean;
  onClick: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      data-testid="otp-sms-instead"
      className={`font-semibold text-[#0A66F0] hover:underline disabled:cursor-not-allowed disabled:no-underline disabled:opacity-50 ${className}`}
      disabled={disabled || cooldown > 0}
      onClick={onClick}
    >
      Send Code By SMS Instead{cooldown > 0 ? ` (${cooldown}s)` : ""}
    </button>
  );
}
