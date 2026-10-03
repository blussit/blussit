/**
 * Only on `npm run dev` (import.meta.env.DEV is false in every production
 * build, so this never ships): a small corner badge saying you're on the
 * local test setup, with the fixed test OTP to type anywhere a code is asked.
 */
export default function LocalEnvBadge() {
  const otp = import.meta.env.VITE_DEV_OTP_HINT;
  return (
    <div className="pointer-events-none fixed left-1/2 top-0 z-[60] -translate-x-1/2 whitespace-nowrap rounded-b-md bg-[#FFF4CD]/95 px-2.5 py-[1px] text-[9.5px] font-semibold text-black shadow-sm">
      LOCAL · test database{otp ? ` · OTP ${otp}` : ""}
    </div>
  );
}
