import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { MapPin, Users } from "lucide-react";
import { coverageLeadApi, type CoverageLead } from "../../api/admin";
import { Badge, Card, CardBody, DataTable } from "../../components/ui";
import { Pager } from "../../components/shared/ListControls";
import { format } from "../../lib/date";
import { toTitle } from "../../lib/titleCase";

/**
 * Demand from areas no service center covers yet — captured on the public
 * landing page the moment a visitor's pincode fails the coverage check,
 * instead of just turning them away. Admin-only by design: this is
 * expansion intelligence ("where should we open next?"), not operational
 * data any center manager acts on.
 */
export default function AdminCoverageLeadsPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["coverage-leads", page],
    queryFn: () => coverageLeadApi.list({ page, page_size: 50 }),
    placeholderData: keepPreviousData,
  });
  const { data: summary } = useQuery({ queryKey: ["coverage-leads-summary"], queryFn: coverageLeadApi.summary });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Coverage Requests</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          People who tried to book from areas we don't serve yet — where demand is waiting for us.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <CardBody className="flex items-center gap-3 p-4">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
              <Users className="h-5 w-5" />
            </span>
            <div>
              <p className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{summary?.total_people ?? "—"}</p>
              <p className="text-xs text-[var(--color-text-secondary)]">People Waiting</p>
            </div>
          </CardBody>
        </Card>
        {(summary?.top_pincodes || []).slice(0, 2).map((t) => (
          <Card key={t.pincode}>
            <CardBody className="flex items-center gap-3 p-4">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--color-accent-light)] text-[var(--color-success)]">
                <MapPin className="h-5 w-5" />
              </span>
              <div>
                <p className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{t.pincode}</p>
                <p className="text-xs text-[var(--color-text-secondary)]">
                  {t.people} {t.people === 1 ? "Person" : "People"} · {t.requests} Request{t.requests === 1 ? "" : "s"}
                </p>
              </div>
            </CardBody>
          </Card>
        ))}
      </div>

      <DataTable<CoverageLead>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No Coverage Requests Yet"
        emptyDescription="When someone from an unserved area tries to book on the website, their details will appear here."
        columns={[
          { header: "Name", accessor: (l) => l.name },
          { header: "Phone", accessor: (l) => <span className="font-mono-num">{l.phone}</span> },
          { header: "Pincode", accessor: (l) => <span className="font-mono-num">{l.pincode}</span> },
          { header: "Area", accessor: (l) => l.city_area || "—" },
          { header: "Interested In", accessor: (l) => toTitle(l.service_interest) || "—" },
          {
            header: "Times Asked",
            accessor: (l) => <Badge tone={l.requests_count > 1 ? "warning" : "neutral"}>{l.requests_count}×</Badge>,
          },
          { header: "Last Asked", accessor: (l) => format(l.last_requested_at) },
        ]}
      />

      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}
    </div>
  );
}
