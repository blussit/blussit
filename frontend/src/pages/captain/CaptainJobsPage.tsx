/**
 * Captain · Jobs — "Upcoming" (still to do, soonest first, server-sorted)
 * and "Done" (newest first, paged). Tap a row to open the job's screen.
 */
import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { bookingApi } from "../../api/booking";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { toSlabs } from "../../lib/bookingGroups";
import { useActiveJobs } from "../../components/captain/CaptainShell";
import { JobListItem } from "../../components/captain/JobListItem";
import { currentCar, needsManager } from "../../components/captain/jobState";
import { Btn, LoadError, PageTitle, Panel } from "../../components/captain/ui";
import { cn } from "../../lib/cn";

const HISTORY_PAGE_SIZE = 20;

export default function CaptainJobsPage() {
  const { t } = useCaptainTranslation();
  const [tab, setTab] = useState<"upcoming" | "done">("upcoming");
  const active = useActiveJobs();
  const history = useInfiniteQuery({
    queryKey: ["my-jobs", "history"],
    queryFn: ({ pageParam }) => bookingApi.myJobs({ scope: "history", status: "completed", page: pageParam, page_size: HISTORY_PAGE_SIZE }),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.meta.page < last.meta.total_pages ? last.meta.page + 1 : undefined),
    enabled: tab === "done",
  });

  const upcoming = toSlabs(active.data?.data ?? []);
  // Missed-window jobs have no captain action — they wait at the bottom.
  const ordered = [...upcoming.filter((s) => !needsManager(currentCar(s))), ...upcoming.filter((s) => needsManager(currentCar(s)))];
  const done = toSlabs(history.data?.pages.flatMap((p) => p.data) ?? []);
  const loading = tab === "upcoming" ? active.isLoading : history.isLoading;
  // A failed read with nothing cached — an error card, never "No jobs".
  const failedQuery = tab === "upcoming" ? active : history;
  const failed = failedQuery.isError && !failedQuery.data;
  const list = tab === "upcoming" ? ordered : done;

  return (
    <div className="space-y-4">
      <PageTitle>{t("captain.v2.tab.jobs")}</PageTitle>
      <div className="grid grid-cols-2 gap-1 rounded-[16px] bg-[#EEF3FA] p-1" role="tablist">
        {(["upcoming", "done"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              "min-h-[44px] rounded-xl text-[15px] font-bold",
              tab === key ? "bg-white text-[#0A66F0] shadow-sm" : "text-[#5F6878]",
            )}
          >
            {key === "upcoming" ? `${t("captain.v2.upcoming")}${upcoming.length ? ` (${upcoming.length})` : ""}` : t("captain.v2.done")}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Panel key={i} className="h-[76px] animate-pulse bg-[#EEF3FA]"><span /></Panel>
          ))}
        </div>
      ) : failed ? (
        <LoadError
          title={t("captain.v2.loadFailed")}
          sub={t("captain.v2.loadFailedSub")}
          retryLabel={t("captain.v2.tryAgain")}
          busy={failedQuery.isFetching}
          onRetry={() => void failedQuery.refetch()}
        />
      ) : !list.length ? (
        <Panel className="px-4 py-10 text-center">
          <p className="text-[15px] font-bold text-[#0E1A33]">{tab === "upcoming" ? t("captain.v2.noJobs") : t("captain.v2.noDone")}</p>
          {tab === "upcoming" && <p className="mt-1 text-sm text-[#5F6878]">{t("captain.v2.noJobsSub")}</p>}
        </Panel>
      ) : (
        <div className="space-y-2">
          {list.map((slab) => (
            <JobListItem key={slab.key} slab={slab} />
          ))}
        </div>
      )}

      {tab === "done" && history.hasNextPage && (
        <Btn variant="outline" className="w-full" loading={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>
          {t("captain.v2.loadMore")}
        </Btn>
      )}
    </div>
  );
}
