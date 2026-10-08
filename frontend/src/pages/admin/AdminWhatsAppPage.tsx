/**
 * WhatsApp CRM & Messaging panel — an ADDITION to the admin dashboard,
 * reusing its shell/theme. One sidebar entry, six internal sections:
 * Inbox (default) / Contacts / Templates / Campaigns / Analytics / Settings.
 */
import { useSearchParams } from "react-router-dom";
import { InboxView } from "../../components/admin/whatsapp/InboxView";
import { AnalyticsView, CampaignsView, ContactsView, TemplatesView, WhatsAppSettingsView } from "../../components/admin/whatsapp/panels";

const TABS = [
  { key: "inbox", label: "Inbox" },
  { key: "contacts", label: "Contacts" },
  { key: "templates", label: "Templates" },
  { key: "campaigns", label: "Campaigns" },
  { key: "analytics", label: "Analytics" },
  { key: "settings", label: "Settings" },
] as const;

export default function AdminWhatsAppPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "inbox";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">WhatsApp</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Customer conversations, templates and messaging health.</p>
        </div>
        <div className="flex max-w-full gap-1 overflow-x-auto rounded-xl border border-[#E4E9F1] bg-white p-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setParams(t.key === "inbox" ? {} : { tab: t.key })}
              className={`shrink-0 whitespace-nowrap rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors ${tab === t.key ? "bg-[var(--color-primary)] text-white" : "text-[var(--color-text-secondary)] hover:bg-[var(--color-primary-light)]"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "inbox" && <InboxView key={params.get("chat") || "inbox"} initialActive={params.get("chat")} />}
      {tab === "contacts" && <ContactsView onOpenChat={(waId) => setParams({ chat: waId })} />}
      {tab === "templates" && <TemplatesView />}
      {tab === "campaigns" && <CampaignsView />}
      {tab === "analytics" && <AnalyticsView />}
      {tab === "settings" && <WhatsAppSettingsView />}
    </div>
  );
}
