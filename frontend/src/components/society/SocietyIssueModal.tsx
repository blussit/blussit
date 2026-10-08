/**
 * A society resident reports a problem with their society service: one
 * preset reason, an optional note, optionally the car. It becomes a normal
 * support ticket (tagged with the society) that the society's manager
 * answers — the reply thread shows up under Help & Support.
 */
import { useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { Modal } from "../ui";
import { getErrorMessage } from "../../lib/api-client";
import { SOCIETY_ISSUE_TYPES, societyIssueApi, type SocietyIssue, type SocietyIssueType } from "../../api/society";
import { titleCase } from "../public/landing/shared";

export interface IssueSociety {
  id: string;
  name: string;
  cars: { vehicle_id: string; registration_number: string; vehicle_type_name?: string | null }[];
}

const chip = (on: boolean) =>
  `min-h-10 rounded-[12px] border px-3 py-2 text-left text-sm font-semibold transition ${
    on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] bg-white text-[#0E1A33] hover:border-[#0A66F0]/50"
  }`;

export function SocietyIssueModal({ open, onClose, societies, onRaised }: {
  open: boolean;
  onClose: () => void;
  societies: IssueSociety[];
  onRaised?: (issue: SocietyIssue) => void;
}) {
  const [societyId, setSocietyId] = useState("");
  const [type, setType] = useState<SocietyIssueType | "">("");
  const [vehicleId, setVehicleId] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<SocietyIssue | null>(null);

  useEffect(() => {
    if (!open) return;
    setSocietyId(societies[0]?.id || "");
    setType("");
    setVehicleId("");
    setNote("");
    setError("");
    setDone(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const society = societies.find((s) => s.id === societyId) || societies[0];
  const cars = society?.cars || [];

  const submit = async () => {
    if (!society) return;
    if (!type) return setError("Pick what went wrong.");
    if (type === "other" && note.trim().length < 5) return setError("Tell us a little about the issue.");
    setBusy(true);
    setError("");
    try {
      const issue = await societyIssueApi.raise({ society_id: society.id, issue_type: type, note: note.trim() || undefined, vehicle_id: vehicleId || undefined });
      setDone(issue);
      onRaised?.(issue);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Report An Issue">
      {done ? (
        <div className="space-y-4 text-center" data-testid="society-issue-done">
          <CheckCircle2 className="mx-auto h-11 w-11 text-[#0A66F0]" />
          <div>
            <p className="text-lg font-bold text-[#0E1A33]">Issue Reported</p>
            <p className="mt-1 text-sm text-[#5F6878]">Your society manager will look into it. Replies show up in Help &amp; Support.</p>
          </div>
          <button type="button" onClick={onClose} className="h-11 w-full rounded-[14px] bg-[#0A66F0] text-[15px] font-bold text-white hover:bg-[#0857CC]">Done</button>
        </div>
      ) : (
        <div className="space-y-4">
          {societies.length > 1 && (
            <div>
              <p className="mb-1.5 text-[13px] font-semibold text-[#0E1A33]">Society</p>
              <div className="flex flex-wrap gap-2">
                {societies.map((s) => (
                  <button key={s.id} type="button" className={chip(s.id === society?.id)} onClick={() => { setSocietyId(s.id); setVehicleId(""); }}>{s.name}</button>
                ))}
              </div>
            </div>
          )}
          <div>
            <p className="mb-1.5 text-[13px] font-semibold text-[#0E1A33]">What Went Wrong?</p>
            <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Issue">
              {SOCIETY_ISSUE_TYPES.map((t) => (
                <button key={t.key} type="button" role="radio" aria-checked={type === t.key} className={chip(type === t.key)} onClick={() => setType(t.key)}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          {cars.length > 0 && (
            <div>
              <p className="mb-1.5 text-[13px] font-semibold text-[#0E1A33]">Which Car? <span className="font-normal text-[#5F6878]">(Optional)</span></p>
              <div className="flex flex-wrap gap-2">
                {cars.map((c) => (
                  <button key={c.vehicle_id} type="button" className={chip(vehicleId === c.vehicle_id)}
                    onClick={() => setVehicleId(vehicleId === c.vehicle_id ? "" : c.vehicle_id)}>
                    <span className="font-mono">{c.registration_number}</span>{c.vehicle_type_name ? ` · ${titleCase(c.vehicle_type_name)}` : ""}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div>
            <label htmlFor="society-issue-note" className="mb-1.5 block text-[13px] font-semibold text-[#0E1A33]">
              Note {type === "other" ? "" : <span className="font-normal text-[#5F6878]">(Optional)</span>}
            </label>
            <textarea id="society-issue-note" rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Not washed on Tuesday"
              className="w-full rounded-[14px] border border-[#E4E9F1] bg-white px-4 py-3 text-[15px] text-[#0E1A33] outline-none placeholder:text-[#9AA3B2] focus:border-[#0A66F0] focus:ring-4 focus:ring-[#0A66F0]/10" />
          </div>
          {error && <p className="rounded-[12px] bg-red-50 px-3 py-2 text-sm text-[var(--color-error)]">{error}</p>}
          <button type="button" disabled={busy || !type} onClick={() => void submit()}
            className="flex h-12 w-full items-center justify-center rounded-[14px] bg-[#0A66F0] text-[15px] font-bold text-white transition hover:bg-[#0857CC] disabled:opacity-50">
            {busy ? "Sending…" : "Send To My Society Manager"}
          </button>
        </div>
      )}
    </Modal>
  );
}
