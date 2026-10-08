/** A read that failed — said plainly, with a way to try again, instead of
 *  an empty state (which reads as a fact: "nothing here") or a spinner that
 *  never ends. */
export function ErrorState({
  message = "Couldn't load this.",
  onRetry,
  busy = false,
  className = "",
}: {
  message?: string;
  onRetry?: () => void;
  busy?: boolean;
  className?: string;
}) {
  return (
    <div role="alert" className={`rounded-2xl border border-[#E4E9F1] bg-white p-6 text-center ${className}`}>
      <p className="text-sm text-[#5F6878]">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          disabled={busy}
          className="mt-3 inline-flex items-center justify-center rounded-[10px] border border-[#E4E9F1] bg-white px-4 py-2 text-sm font-semibold text-[#0A66F0] transition-colors hover:border-[#0A66F0] disabled:opacity-60"
        >
          {busy ? "Trying…" : "Try Again"}
        </button>
      )}
    </div>
  );
}
