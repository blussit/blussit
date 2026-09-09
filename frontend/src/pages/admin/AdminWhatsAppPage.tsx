/**
 * WhatsApp CRM & Messaging panel — an ADDITION to the admin dashboard,
 * reusing its shell/theme. One sidebar entry, five internal sections:
 * Inbox (default) / Contacts / Templates / Campaigns / Analytics.
 */
import { useSearchParams } from "react-router-dom";
import { InboxView } from "../../components/admin/whatsapp/InboxView";
import { AnalyticsView, CampaignsView, ContactsView, TemplatesView } from "../../components/admin/whatsapp/panels";

const TABS = [
  { key: "inbox", label: "Inbox" },
  { key: "contacts", label: "Contacts" },
  { key: "templates", label: "Templates" },
  { key: "campaigns", label: "Campaigns" },
  { key: "analytics", label: "Analytics" },
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
        <div className="flex gap-1 rounded-xl bg-white p-1 shadow-[var(--shadow-soft)]">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setParams(t.key === "inbox" ? {} : { tab: t.key })}
              className={`rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors ${tab === t.key ? "bg-[var(--color-primary)] text-white" : "text-[var(--color-text-secondary)] hover:bg-[var(--color-primary-light)]"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "inbox" && <InboxView />}
      {tab === "contacts" && <ContactsView onOpenChat={() => setParams({})} />}
      {tab === "templates" && <TemplatesView />}
      {tab === "campaigns" && <CampaignsView />}
      {tab === "analytics" && <AnalyticsView />}
    </div>
  );
}
