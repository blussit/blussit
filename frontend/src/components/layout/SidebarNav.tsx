import { useEffect, useId, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { ChevronDown, type LucideIcon } from "lucide-react";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  end?: boolean;
  /** Small red count pill after the label (e.g. unread WhatsApp chats). */
  badge?: number;
  /** A plain red dot after the label ("something new here"), no number. */
  dot?: boolean;
}

/** A collapsible dropdown of nav items. */
export interface NavGroup {
  label: string;
  icon: LucideIcon;
  children: NavItem[];
}

export type NavEntry = NavItem | NavGroup;

export const isNavGroup = (entry: NavEntry): entry is NavGroup => "children" in entry;

/** Every real link, groups flattened (tab title, tests, active-route lookup). */
export function flattenNav(entries: NavEntry[]): NavItem[] {
  return entries.flatMap((e) => (isNavGroup(e) ? e.children : [e]));
}

const matches = (item: NavItem, pathname: string) =>
  item.end ? pathname === item.to : pathname === item.to || pathname.startsWith(`${item.to}/`);

const linkClass = (isActive: boolean, nested: boolean) =>
  `flex items-center gap-3 rounded-[10px] ${nested ? "px-3 py-2 text-[13.5px]" : "px-3.5 py-2.5 text-[14px]"} font-medium transition-colors ${
    isActive ? "bg-[#E8F0FE] font-semibold text-[#0A66F0]" : "text-[#5F6878] hover:bg-[#F3F6FA] hover:text-[#0E1A33]"
  }`;

const Dot = ({ label }: { label: string }) => (
  <span className="ml-auto h-2 w-2 shrink-0 rounded-full bg-[var(--color-error)]" role="img" aria-label={label} />
);

const Count = ({ n }: { n: number }) => (
  <span className="ml-auto inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-[var(--color-error)] px-1.5 py-0.5 text-[10px] font-bold text-white">
    {n > 99 ? "99+" : n}
  </span>
);

function Marker({ item }: { item: NavItem }) {
  if (item.badge != null && item.badge > 0) return <Count n={item.badge} />;
  if (item.dot) return <Dot label="New" />;
  return null;
}

const STORAGE_PREFIX = "admin_nav_groups:";

function readOpen(key: string): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + key);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" ? (parsed as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

/**
 * The sidebar links. Plain items render as before; a group renders as a
 * button (aria-expanded) that opens / closes its links. Open / closed state is
 * remembered per portal in localStorage, the group holding the current page
 * opens by itself, and a group shows a red dot (or the summed count) while
 * any link inside it has one — also when it is collapsed.
 */
export function SidebarNav({ entries, storageKey, onNavigate }: { entries: NavEntry[]; storageKey: string; onNavigate: () => void }) {
  const { pathname } = useLocation();
  const baseId = useId();
  const [open, setOpen] = useState<Record<string, boolean>>(() => readOpen(storageKey));

  const activeGroup = useMemo(
    () => entries.find((e): e is NavGroup => isNavGroup(e) && e.children.some((c) => matches(c, pathname)))?.label,
    [entries, pathname],
  );

  // Landing on a page (a link, the browser back button, a notification) opens its group.
  useEffect(() => {
    if (activeGroup) setOpen((o) => (o[activeGroup] ? o : { ...o, [activeGroup]: true }));
  }, [activeGroup]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_PREFIX + storageKey, JSON.stringify(open));
    } catch {
      /* private mode / blocked storage: the menu simply starts closed next time */
    }
  }, [open, storageKey]);

  return (
    <>
      {entries.map((entry) => {
        if (!isNavGroup(entry)) {
          return (
            <NavLink key={entry.to} to={entry.to} end={entry.end} onClick={onNavigate} className={({ isActive }) => linkClass(isActive, false)}>
              <entry.icon className="h-[18px] w-[18px]" />
              {entry.label}
              <Marker item={entry} />
            </NavLink>
          );
        }
        const isOpen = !!open[entry.label];
        const count = entry.children.reduce((sum, c) => sum + (c.badge || 0), 0);
        const anyDot = count > 0 || entry.children.some((c) => c.dot);
        const holdsPage = activeGroup === entry.label;
        const panelId = `${baseId}-${entry.label.replace(/\W+/g, "-")}`;
        return (
          <div key={entry.label} className="flex flex-col gap-0.5">
            <button
              type="button"
              aria-expanded={isOpen}
              aria-controls={panelId}
              onClick={() => setOpen((o) => ({ ...o, [entry.label]: !o[entry.label] }))}
              className={`flex w-full items-center gap-3 rounded-[10px] px-3.5 py-2.5 text-left text-[14px] font-medium transition-colors hover:bg-[#F3F6FA] hover:text-[#0E1A33] ${
                holdsPage ? "text-[#0A66F0]" : "text-[#5F6878]"
              }`}
            >
              <entry.icon className="h-[18px] w-[18px]" />
              <span className="min-w-0 flex-1 truncate">{entry.label}</span>
              {count > 0 ? <Count n={count} /> : anyDot ? <Dot label="New requests inside" /> : null}
              <ChevronDown className={`h-4 w-4 shrink-0 transition-transform ${isOpen ? "rotate-180" : ""}`} aria-hidden="true" />
            </button>
            {isOpen && (
              <div id={panelId} role="group" aria-label={entry.label} className="ml-4 flex flex-col gap-0.5 border-l border-[#E4E9F1] pl-2">
                {entry.children.map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.end} onClick={onNavigate} className={({ isActive }) => linkClass(isActive, true)}>
                    <item.icon className="h-4 w-4" />
                    <span className="min-w-0 truncate">{item.label}</span>
                    <Marker item={item} />
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
