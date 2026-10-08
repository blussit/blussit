/**
 * "Society requests" — housing societies that asked for Blussit from the
 * landing page. Manager: their center's (routed by pincode). Admin: all,
 * including pincodes no center serves yet. Work them new → contacted →
 * registered / closed; "Register" opens the society form prefilled.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Phone } from "lucide-react";
import { Badge, Button, ErrorState, Panel, Select, Spinner } from "../ui";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import { societyLeadApi, type SocietyInput, type SocietyLead, type SocietyLeadStatus } from "../../api/society";

const STATUS: Record<SocietyLeadStatus, { label: string; tone: "warning" | "info" | "success" | "neutral" }> = {
  new: { label: "New", tone: "warning" },
  contacted: { label: "Contacted", tone: "info" },
  registered: { label: "Registered", tone: "success" },
  closed: { label: "Closed", tone: "neutral" },
};

export function SocietyRequestsPanel({ isAdmin = false, onRegister }: { isAdmin?: boolean; onRegister: (prefill: Partial<SocietyInput>) => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState("open");
  const [center, setCenter] = useState("");
  const q = useQuery({
    queryKey: ["society-leads", status, center],
    queryFn: () => societyLeadApi.list({ status: status || undefined, center_id: center || undefined }),
  });
  const update = useMutation({
    mutationFn: ({ id, next }: { id: string; next: SocietyLeadStatus }) => societyLeadApi.update(id, { status: next }),
    onSuccess: () => { toast.push({ tone: "success", title: "Saved" }); queryClient.invalidateQueries({ queryKey: ["society-leads"] }); },
    onError: (err) => toast.push({ tone: "error", title: "That didn't work", message: getErrorMessage(err) }),
  });
  const counts = q.data?.counts;
  const open = (counts?.new ?? 0) + (counts?.contacted ?? 0);

  const register = (l: SocietyLead) =>
    onRegister({
      lead_id: l.id,
      name: l.society_name,
      area: l.area,
      pincode: l.pincode,
      contact_name: l.contact_name,
      contact_phone: l.phone,
      address_line: l.area,
      notes: [l.approx_cars ? `About ${l.approx_cars} cars` : "", l.note || ""].filter(Boolean).join(" · ").slice(0, 500),
    });

  return (
    <Panel
      title={`Society Requests${open ? ` · ${open} Open` : ""}`}
      description="Societies that asked for Blussit from the website. Call them, then register the society."
      actions={
        <div className="flex gap-2">
          {isAdmin && (
            <Select aria-label="Center" value={center} onChange={(e) => setCenter(e.target.value)}>
              <option value="">All Centers</option>
              <option value="unassigned">No Center (Pincode Not Served)</option>
            </Select>
          )}
          <Select aria-label="Request status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">Open</option>
            <option value="new">New</option>
            <option value="contacted">Contacted</option>
            <option value="registered">Registered</option>
            <option value="closed">Closed</option>
            <option value="">All</option>
          </Select>
        </div>
      }
    >
      {q.isLoading ? <Spinner /> : q.isError && !q.data ? (
        <ErrorState message="Couldn't load society requests." onRetry={() => void q.refetch()} busy={q.isFetching} className="p-4" />
      ) : !q.data?.rows.length ? (
        <p className="py-6 text-center text-sm font-semibold text-black">{status === "open" ? "No Open Requests" : "Nothing Here"}</p>
      ) : (
        <ul className="divide-y divide-[#E4E9F1]" data-testid="society-requests">
          {q.data.rows.map((l) => (
            <li key={l.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between" data-testid="society-request">
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 font-semibold text-black">
                  <Building2 className="h-4 w-4 shrink-0 text-[#0A66F0]" /> {l.society_name}
                  <Badge tone={STATUS[l.status].tone}>{STATUS[l.status].label}</Badge>
                  {l.requests_count > 1 && <span className="text-xs font-normal text-gray-500">Asked {l.requests_count}×</span>}
                </p>
                <p className="mt-0.5 text-xs text-gray-500">
                  {l.area} · {l.pincode} · about {l.approx_cars} cars{isAdmin ? ` · ${l.service_center_name || "no center"}` : ""} · {format(l.created_at || "")}
                </p>
                <p className="mt-0.5 flex items-center gap-1 text-xs text-gray-600">
                  <Phone className="h-3.5 w-3.5" /> {l.contact_name} · <a className="font-semibold text-[#0A66F0]" href={`tel:+91${l.phone}`}>{l.phone}</a>
                  {l.note ? <span className="truncate"> · {l.note}</span> : null}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Select aria-label={`Status of ${l.society_name}`} value={l.status} disabled={update.isPending}
                  onChange={(e) => update.mutate({ id: l.id, next: e.target.value as SocietyLeadStatus })}>
                  <option value="new">New</option>
                  <option value="contacted">Contacted</option>
                  <option value="registered">Registered</option>
                  <option value="closed">Closed</option>
                </Select>
                {l.status !== "registered" && (isAdmin || l.service_center_id) && (
                  <Button size="sm" onClick={() => register(l)}>Register</Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
