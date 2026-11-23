import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { adminInboxApi, type InboxSection } from "../api/admin";

/**
 * Behind the red dots of the admin inbox pages (Contact Messages, Custom Plan
 * Requests, Coverage Requests).
 *
 * Opening the page shows what is NEW (`seen_at === null`) with a "New" mark and
 * tells the server those rows were seen, so the sidebar dot clears; the marks
 * stay on screen for this visit (they are remembered here) and are gone the
 * next time. "Mark All As Read" also covers rows on other pages.
 */
export function useInboxSeen<T extends { id: string; seen_at?: string | null }>(section: InboxSection, rows: T[] | undefined) {
  const queryClient = useQueryClient();
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  const reported = useRef<Set<string>>(new Set());

  // Shared with the sidebar (same key): how many rows of this section are still unseen anywhere.
  const { data: badges } = useQuery({ queryKey: ["admin-nav-badges"], queryFn: adminInboxApi.badges, staleTime: 15_000 });

  const markSeen = useMutation({
    mutationFn: (ids?: string[]) => adminInboxApi.markSeen(section, ids),
    onSuccess: (res) => queryClient.setQueryData(["admin-nav-badges"], res.counts),
    onError: () => void queryClient.invalidateQueries({ queryKey: ["admin-nav-badges"] }),
  });

  useEffect(() => {
    const unseen = (rows || []).filter((r) => r.seen_at === null).map((r) => r.id);
    const added = unseen.filter((id) => !reported.current.has(id));
    if (!added.length) return;
    added.forEach((id) => reported.current.add(id));
    setFresh((prev) => new Set([...prev, ...added]));
    markSeen.mutate(added.slice(0, 200));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  const markAll = useCallback(() => {
    setFresh(new Set());
    markSeen.mutate(undefined);
  }, [markSeen]);

  const unseenTotal = badges?.[section] ?? 0;
  return {
    isNew: (id: string) => fresh.has(id),
    newCount: fresh.size,
    /** Nothing to clear: no row is marked New and nothing is unseen on the server. */
    allRead: fresh.size === 0 && unseenTotal === 0,
    markAll,
    markingAll: markSeen.isPending,
  };
}
