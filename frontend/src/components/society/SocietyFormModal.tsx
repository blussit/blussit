import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MapPin } from "lucide-react";
import { Button, Input, Modal, Select } from "../ui";
import { MapPicker } from "../shared/MapPicker";
import { adminServiceCenterApi } from "../../api/admin";
import { societyApi, type SocietyDetail, type SocietyInput } from "../../api/society";
import { getErrorMessage } from "../../lib/api-client";
import { validateIndianMobile } from "../../lib/validators";

/** Register (or edit) a society: name, address, map pin, contact. The
 * service center is resolved from the pin on the server. */
export function SocietyFormModal({
  open,
  onClose,
  onSaved,
  society,
  isAdmin = false,
  prefill,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (s: SocietyDetail) => void;
  society?: SocietyDetail | null;
  isAdmin?: boolean;
  /** "Register this society" from a society request: its details (and
   *  lead_id, so the request is marked registered on save). */
  prefill?: Partial<SocietyInput> | null;
}) {
  const [form, setForm] = useState<SocietyInput>({ ...blank(society), ...(prefill || {}) });
  const [coords, setCoords] = useState(society?.latitude != null ? `${society.latitude}, ${society.longitude}` : "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const centers = useQuery({
    queryKey: ["admin-centers-all"],
    queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }),
    enabled: isAdmin && open && !society,
  });

  useEffect(() => {
    if (open) {
      setForm({ ...blank(society), ...(society ? {} : prefill || {}) });
      setCoords(society?.latitude != null ? `${society.latitude}, ${society.longitude}` : "");
      setError("");
    }
  }, [open, society, prefill]);

  const set = (patch: Partial<SocietyInput>) => setForm((f) => ({ ...f, ...patch }));
  const pin = (lat: number, lng: number) => {
    set({ latitude: Number(lat.toFixed(6)), longitude: Number(lng.toFixed(6)) });
    setCoords(`${lat.toFixed(6)}, ${lng.toFixed(6)}`);
  };
  const parseCoords = (text: string) => {
    setCoords(text);
    const m = text.match(/(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)/);
    if (m) set({ latitude: Number(m[1]), longitude: Number(m[2]) });
  };

  const save = async () => {
    if (form.name.trim().length < 2) return setError("Enter the society name.");
    if (form.address_line.trim().length < 3) return setError("Enter the address.");
    if (!/^\d{6}$/.test(form.pincode)) return setError("Enter the 6-digit pincode.");
    if (!form.latitude || !form.longitude) return setError("Drop the pin on the map (or paste the coordinates).");
    if (form.contact_phone && !validateIndianMobile(form.contact_phone)) return setError("Contact mobile looks wrong.");
    setSaving(true);
    setError("");
    try {
      const payload: SocietyInput = {
        ...form,
        contact_phone: form.contact_phone ? validateIndianMobile(form.contact_phone)! : undefined,
        service_center_id: isAdmin && form.service_center_id ? form.service_center_id : undefined,
      };
      const saved = society ? await societyApi.update(society.id, payload) : await societyApi.create(payload);
      onSaved(saved);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={society ? "Edit Society" : "Register A Society"} maxWidth="max-w-2xl">
      <div className="space-y-3">
        {!society && prefill?.lead_id && (
          <p className="rounded-xl bg-[#EEF3FA] px-3 py-2 text-sm text-[#0E1A33]">From a society request — check the details, drop the gate pin, and save. The request is marked registered.</p>
        )}
        <Input label="Society Name" value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Green Acres Phase 2" />
        <Input label="Address" value={form.address_line} onChange={(e) => set({ address_line: e.target.value })} placeholder="Street, landmark" />
        <div className="grid gap-3 sm:grid-cols-3">
          <Input label="Area" value={form.area || ""} onChange={(e) => set({ area: e.target.value })} placeholder="Vijay Nagar" />
          <Input label="City" value={form.city || ""} onChange={(e) => set({ city: e.target.value })} />
          <Input label="Pincode" value={form.pincode} onChange={(e) => set({ pincode: e.target.value.replace(/\D/g, "").slice(0, 6) })} inputMode="numeric" />
        </div>
        <div>
          <p className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]"><MapPin className="h-4 w-4" /> Society Gate Pin</p>
          <MapPicker latitude={form.latitude || null} longitude={form.longitude || null} onChange={pin} showUseMyLocation
            onAddressResolved={(a) => set({ pincode: form.pincode || a.pincode, city: form.city || a.city })} />
          <Input className="mt-2" aria-label="Coordinates" value={coords} onChange={(e) => parseCoords(e.target.value)} placeholder="Or paste coordinates: 22.7196, 75.8577" />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Input label="Contact Person" value={form.contact_name || ""} onChange={(e) => set({ contact_name: e.target.value })} placeholder="Secretary / RWA" />
          <Input label="Contact Mobile" value={form.contact_phone || ""} onChange={(e) => set({ contact_phone: e.target.value })} inputMode="tel" />
        </div>
        {isAdmin && !society && (
          <Select label="Service Center" value={form.service_center_id || ""} onChange={(e) => set({ service_center_id: e.target.value })}>
            <option value="">From The Pin (Automatic)</option>
            {(centers.data?.data || []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
        )}
        <Input label="Notes" value={form.notes || ""} onChange={(e) => set({ notes: e.target.value })} placeholder="Gate timings, parking…" />
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} isLoading={saving}>{society ? "Save" : "Register Society"}</Button>
        </div>
      </div>
    </Modal>
  );
}

function blank(s?: SocietyDetail | null): SocietyInput {
  return {
    name: s?.name || "",
    address_line: s?.address_line || "",
    area: s?.area || "",
    city: s?.city || "Indore",
    state: s?.state || "Madhya Pradesh",
    pincode: s?.pincode || "",
    latitude: s?.latitude ?? 0,
    longitude: s?.longitude ?? 0,
    contact_name: s?.contact_name || "",
    contact_phone: s?.contact_phone || "",
    notes: s?.notes || "",
  };
}
