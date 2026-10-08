/**
 * My Wallet (/app/wallet — WhatsApp wallet messages link here). One balance
 * per customer that can go negative (spec 2026-10-07 §1.1):
 *   - credit (refund from a cancelled paid booking, an extra payment, a
 *     price cut) is used automatically on the next booking;
 *   - a balance due (a late-cancellation charge on an unpaid booking) is
 *     added to the next booking;
 *   - money back to the bank/UPI is done by the manager — the customer
 *     asks on WhatsApp (no in-app request).
 * Then the ledger, newest first, 20 at a time.
 */
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowDownLeft, ArrowUpRight, CalendarCheck, Landmark, Wallet } from "lucide-react";
import { customerWalletMeApi, MY_WALLET_QUERY_KEY, type CustomerWalletEntry } from "../../api/customerWalletMe";
import { btn, card, PageHeader, Skeleton, WhatsAppGlyph } from "../../components/customer/ui";
import { paybackLine, rupees, walletEntryTitle } from "../../components/customer/money";
import { openBlussitWhatsApp } from "../../components/public/WhatsAppFloatingButton";
import { useAuth } from "../../context/AuthContext";
import { asUtcInstant, formatDateTime } from "../../lib/date";
import { cn } from "../../lib/cn";

const PAGE_SIZE = 20;

function BalanceCard({ balance, owedNext, owedCarried }: { balance: number; owedNext: number; owedCarried: number }) {
  const amount = Math.round(balance);
  if (amount < 0) {
    const owe = Math.abs(amount);
    return (
      <section className="rounded-2xl border border-[#F6D3D3] bg-[#FFF5F5] p-5" data-testid="wallet-balance" data-tone="due">
        <p className="text-sm font-semibold text-[#C62828]">Balance Due</p>
        <p className="mt-1 font-display text-[34px] font-bold leading-none text-[#C62828] tabular-nums">You Owe ₹{owe}</p>
        <p className="mt-2 text-sm text-[#0E1A33]">
          {owedNext > 0 || owedCarried <= 0 ? "Added To Your Next Booking" : "Added To Your Upcoming Booking"}
        </p>
      </section>
    );
  }
  if (amount > 0) {
    return (
      <section className="rounded-2xl border border-[#CBEBD6] bg-[#F1FAF4] p-5" data-testid="wallet-balance" data-tone="credit">
        <p className="text-sm font-semibold text-[#1E7B3C]">Wallet Credit</p>
        <p className="mt-1 font-display text-[34px] font-bold leading-none text-[#1E7B3C] tabular-nums">₹{amount}</p>
        <p className="mt-2 text-sm text-[#0E1A33]">Used Automatically On Your Next Booking</p>
      </section>
    );
  }
  return (
    <section className={`${card} p-5`} data-testid="wallet-balance" data-tone="zero">
      <p className="text-sm font-semibold text-[#5F6878]">Wallet Balance</p>
      <p className="mt-1 font-display text-[34px] font-bold leading-none text-[#0E1A33] tabular-nums">₹0</p>
      <p className="mt-2 text-sm text-[#5F6878]">Nothing to use or pay right now.</p>
    </section>
  );
}

function EntryRow({ e }: { e: CustomerWalletEntry }) {
  const credit = e.amount >= 0;
  const Icon = credit ? ArrowDownLeft : ArrowUpRight;
  const when = e.created_at ? formatDateTime(asUtcInstant(e.created_at)) : "";
  const payout = e.kind === "payout" ? paybackLine(e) : "";
  return (
    <li className="flex items-start gap-3 px-4 py-3.5">
      <span className={cn("mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", credit ? "bg-[#E7F6EC] text-[#1E7B3C]" : "bg-[#FDECEC] text-[#C62828]")}>
        <Icon className="h-[18px] w-[18px]" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-semibold leading-snug text-[#0E1A33]">{walletEntryTitle(e)}</p>
        <p className="mt-0.5 text-xs text-[#5F6878]">
          {e.booking_id && e.booking_number ? (
            <>
              <Link to={`/app/bookings/${e.booking_id}`} className="font-semibold tabular-nums text-[#0A66F0] hover:underline">
                {e.booking_number}
              </Link>
              {when && " · "}
            </>
          ) : e.booking_number ? (
            <span className="tabular-nums">{e.booking_number} · </span>
          ) : null}
          {when}
        </p>
        {payout && <p className="mt-0.5 text-xs text-[#5F6878]">{payout}</p>}
        {e.note && <p className="mt-0.5 line-clamp-2 text-xs text-[#5F6878]">{e.note}</p>}
      </div>
      <div className="shrink-0 text-right">
        <p className={cn("tabular-nums text-[15px] font-bold", credit ? "text-[#1E7B3C]" : "text-[#C62828]")}>
          {credit ? "+" : "−"}
          {rupees(e.amount)}
        </p>
        <p className="mt-0.5 whitespace-nowrap text-[11px] text-[#8A94A6] tabular-nums">
          Balance {e.balance_after < 0 ? "−" : ""}
          {rupees(e.balance_after)}
        </p>
      </div>
    </li>
  );
}

export default function WalletPage() {
  const { user } = useAuth();
  const query = useInfiniteQuery({
    queryKey: [...MY_WALLET_QUERY_KEY, "ledger"],
    queryFn: ({ pageParam }) => customerWalletMeApi.me({ page: pageParam, page_size: PAGE_SIZE }),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page * last.page_size < last.total ? last.page + 1 : undefined),
  });
  const first = query.data?.pages[0];
  const entries = query.data?.pages.flatMap((p) => p.items) ?? [];
  const balance = first?.balance ?? 0;
  const credit = Math.round(first?.credit_available ?? 0);

  const requestTransfer = () =>
    openBlussitWhatsApp(
      `Hi Blussit, please send my wallet credit of ₹${credit} to my bank/UPI.${user?.phone ? ` My number: ${user.phone}.` : ""}`
    );

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <PageHeader back="/app/profile" title="My Wallet" />

      {query.isLoading ? (
        <Skeleton className="h-[132px]" />
      ) : query.isError && !first ? (
        <div className={`${card} p-6 text-center`}>
          <p className="text-sm text-[#5F6878]">Couldn't load your wallet.</p>
          <button type="button" className={btn("outline", "sm", "mt-3")} disabled={query.isFetching} onClick={() => void query.refetch()}>
            {query.isFetching ? "Trying…" : "Try Again"}
          </button>
        </div>
      ) : (
        <BalanceCard balance={balance} owedNext={first?.previous_balance_due ?? 0} owedCarried={first?.carried_due ?? 0} />
      )}

      <section className={`${card} p-5`}>
        <h2 className="font-display text-[16px] font-bold text-[#0E1A33]">How It Works</h2>
        <ul className="mt-3 space-y-3 text-sm text-[#0E1A33]">
          <li className="flex gap-3">
            <Wallet className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" />
            <span>Credit is used automatically on your next booking.</span>
          </li>
          <li className="flex gap-3">
            <CalendarCheck className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" />
            <span>A balance due is added to your next booking.</span>
          </li>
          <li className="flex gap-3">
            <Landmark className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" />
            <span>Want your credit back in your bank or UPI? Ask your manager — they send it to you.</span>
          </li>
        </ul>
        {credit > 0 && (
          <button type="button" onClick={requestTransfer} className={btn("outline", "md", "mt-4 w-full")} data-testid="wallet-request-transfer">
            <WhatsAppGlyph className="h-[18px] w-[18px]" /> Request Transfer
          </button>
        )}
        <p className="mt-3 text-xs text-[#5F6878]">
          <Link to="/cancellation-policy" className="font-semibold text-[#0A66F0] hover:underline">
            Cancellation Policy
          </Link>
        </p>
      </section>

      <section>
        <h2 className="mb-3 font-display text-[16px] font-bold text-[#0E1A33]">History</h2>
        {query.isLoading ? (
          <Skeleton className="h-[180px]" />
        ) : !entries.length ? (
          query.isError ? null : (
            <div className={`${card} p-6 text-center text-sm text-[#5F6878]`}>No wallet activity yet.</div>
          )
        ) : (
          <>
            <ul className={`${card} divide-y divide-[#EEF2F7] overflow-hidden`} data-testid="wallet-ledger">
              {entries.map((e) => (
                <EntryRow key={e.id} e={e} />
              ))}
            </ul>
            {query.hasNextPage && (
              <div className="mt-3 flex flex-col items-center gap-1.5">
                <button type="button" className={btn("outline", "md", "w-full sm:w-auto sm:min-w-[200px]")} disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                  {query.isFetchingNextPage ? "Loading…" : "Load More"}
                </button>
                <p className="text-xs text-[#8A94A6]">
                  Showing {entries.length} of {first?.total ?? entries.length}
                </p>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
