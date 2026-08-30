import { useQuery } from "@tanstack/react-query";
import { MapPin, Users } from "lucide-react";
import { coverageLeadApi, type CoverageLead } from "../../api/admin";
import { Badge, Card, CardBody, DataTable } from "../../components/ui";
import { format } from "../../lib/date";

/**
 * Demand from areas no service center covers yet — captured on the public
 * landing page the moment a visitor's pincode fails the coverage check,
 * instead of just turning them away. Admin-only by design: this is
 * expansion intelligence ("where should we open next?"), not operational
 * data any center manager acts on.
 */
export default function AdminCoverageLeadsPage() {
  const { data, isLoading } = useQuery({ queryKey: ["coverage-leads"], queryFn: () => coverageLeadApi.list({ page: 1, page_size: 100 }) });
  const { data: summary } = useQuery({ queryKey: ["coverage-leads-summary"], queryFn: coverageLeadApi.summary });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Coverage requests</h1>
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
              <p className="text-xs text-[var(--color-text-secondary)]">People waiting</p>
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
                  {t.people} {t.people === 1 ? "person" : "people"} · {t.requests} request{t.requests === 1 ? "" : "s"}
                </p>
              </div>
            </CardBody>
          </Card>
        ))}
      </div>

      <DataTable<CoverageLead>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No coverage requests yet"
        emptyDescription="When someone from an unserved area tries to book on the website, their details will appear here."
        columns={[
          { header: "Name", accessor: (l) => l.name },
          { header: "Phone", accessor: (l) => <span className="font-mono-num">{l.phone}</span> },
          { header: "Pincode", accessor: (l) => <span className="font-mono-num">{l.pincode}</span> },
          { header: "Area", accessor: (l) => l.city_area || "—" },
          { header: "Interested in", accessor: (l) => l.service_interest || "—" },
          {
            header: "Times asked",
            accessor: (l) => <Badge tone={l.requests_count > 1 ? "warning" : "neutral"}>{l.requests_count}×</Badge>,
          },
          { header: "Last asked", accessor: (l) => format(l.last_requested_at) },
        ]}
      />
    </div>
  );
}
