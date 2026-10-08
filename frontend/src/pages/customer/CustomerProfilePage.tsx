/**
 * Customer Profile — who you are, then one list to everything else: garage,
 * bookings, plans, offers, notifications, help, settings, log out. The
 * editable bits (name, WhatsApp reminders switch, addresses, password) live
 * on /app/settings. No "payment methods": we never store cards.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bell, CalendarDays, CarFront, Check, Gift, LifeBuoy, LogOut, Pencil, Settings, Tag, Wallet } from "lucide-react";
import { customerWalletMeApi, MY_WALLET_QUERY_KEY } from "../../api/customerWalletMe";
import { notificationApi, subscriptionApi } from "../../api/engagement";
import { vehicleApi } from "../../api/profile";
import { card, MenuRow, PageHeader } from "../../components/customer/ui";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { isLivePass, passState } from "../../lib/passState";

/** "9876543210" → "+91 98765 43210". */
function prettyPhone(phone?: string | null): string {
  const digits = String(phone || "").replace(/\D/g, "");
  const local = digits.length === 12 && digits.startsWith("91") ? digits.slice(2) : digits;
  return local.length === 10 ? `+91 ${local.slice(0, 5)} ${local.slice(5)}` : phone || "";
}

export default function CustomerProfilePage() {
  const { user, logout } = useAuth();
  const confirm = useConfirm();
  const { data: subs } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const { data: vehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list, staleTime: 60_000 });
  const { data: wallet } = useQuery({ queryKey: [...MY_WALLET_QUERY_KEY, "summary"], queryFn: () => customerWalletMeApi.me({ page: 1, page_size: 1 }), staleTime: 60_000 });
  const walletBalance = Math.round(wallet?.balance ?? 0);
  const walletMeta = !wallet ? undefined : walletBalance > 0 ? (
    <span className="font-semibold text-[#1E7B3C]">₹{walletBalance} Credit</span>
  ) : walletBalance < 0 ? (
    <span className="font-semibold text-[#C62828]">You Owe ₹{-walletBalance}</span>
  ) : (
    "₹0"
  );
  // Same cache entry the shell polls — no extra request.
  const { data: notifications } = useQuery({ queryKey: ["notifications", "unread"], queryFn: () => notificationApi.list({ page: 1, page_size: 20 }) });

  const live = (subs || []).filter(isLivePass);
  const active = live.filter((s) => passState(s) === "active");
  const plansMeta = !live.length ? "No Active Plan" : active.length === 1 ? `${active[0].remaining_service_count ?? 0} Washes Left` : `${live.length} Plans`;
  const initial = (user?.full_name || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <PageHeader title="Profile" />

      <Link to="/app/settings" className={`${card} flex items-center gap-4 p-4 transition-shadow hover:shadow-[0_12px_32px_-16px_rgba(14,26,51,0.28)]`}>
        {user?.profile_image ? (
          <img src={user.profile_image} alt="" className="h-16 w-16 shrink-0 rounded-full object-cover ring-4 ring-[#EEF3FA]" />
        ) : (
          <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-[#E8F0FE] font-display text-2xl font-bold text-[#0A66F0] ring-4 ring-[#F5F8FD]">{initial}</span>
        )}
        <div className="min-w-0 flex-1">
          <p className="break-words font-display text-[19px] font-bold leading-snug text-[#0E1A33]">{user?.full_name}</p>
          <div className="mt-0.5 flex flex-wrap items-center gap-2">
            {user?.phone && <span className="tabular-nums text-sm text-[#5F6878]">{prettyPhone(user.phone)}</span>}
            {user?.phone && user?.phone_verified && (
              <span className="inline-flex items-center gap-1 rounded-full bg-[#E7F6EC] px-2 py-0.5 text-[11px] font-semibold text-[#1E7B3C]">
                <Check className="h-2.5 w-2.5" strokeWidth={3.5} /> Verified
              </span>
            )}
          </div>
          {user?.email && <p className="truncate text-xs text-[#8A94A6]">{user.email}</p>}
        </div>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[#E4E9F1] text-[#5F6878]" aria-hidden>
          <Pencil className="h-4 w-4" />
        </span>
      </Link>

      <div className={`${card} divide-y divide-[#EEF2F7] overflow-hidden`}>
        <MenuRow icon={CarFront} label="My Garage" meta={vehicles?.length ? `${vehicles.length} Saved` : undefined} to="/app/garage" />
        <MenuRow icon={CalendarDays} label="My Bookings" to="/app/bookings" />
        <MenuRow icon={Gift} label="My Plans" meta={subs ? plansMeta : undefined} to="/app/subscriptions" />
        <MenuRow icon={Wallet} label="My Wallet" meta={walletMeta} to="/app/wallet" />
        <MenuRow icon={Tag} label="Offers" to="/app/offers" />
        <MenuRow icon={Bell} label="Notifications" badge={notifications?.unread_count || 0} to="/app/notifications" />
        <MenuRow icon={LifeBuoy} label="Help & Support" to="/app/support" />
        <MenuRow icon={Settings} label="Settings" to="/app/settings" />
      </div>

      <div className={`${card} overflow-hidden`}>
        <MenuRow
          icon={LogOut}
          label="Log Out"
          danger
          onClick={async () => {
            if (await confirm({ title: "Log Out?", message: "You'll sign back in with an OTP on your phone.", confirmLabel: "Log Out", tone: "default" })) logout();
          }}
        />
      </div>
    </div>
  );
}
