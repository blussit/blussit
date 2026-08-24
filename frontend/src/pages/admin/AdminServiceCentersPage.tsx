import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LocateFixed, Pencil, Plus, Power } from "lucide-react";
import { adminServiceCenterApi, adminUserApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal, Select } from "../../components/ui";
import { MapPicker } from "../../components/shared/MapPicker";
import { getErrorMessage } from "../../lib/api-client";
import type { ServiceCenter } from "../../types";

const emptyForm = {
  name: "",
  address: "",
  city: "",
  state: "",
  pincode: "",
  latitude: "",
  longitude: "",
  radius_km: "6",
  service_pincodes: "",
  contact_phone: "",
  contact_email: "",
  manager_id: "",
  working_hours_start: "08:00",
  working_hours_end: "20:00",
};

export default function AdminServiceCentersPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["admin-centers"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 50 }) });
  const { data: managers } = useQuery({ queryKey: ["admin-managers"], queryFn: () => adminUserApi.list({ role: "manager", page: 1, page_size: 100 }) });

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ServiceCenter | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [locating, setLocating] = useState(false);

  const buildPayload = () => ({
    name: form.name,
    location: {
      address: form.address,
      city: form.city,
      state: form.state,
      pincode: form.pincode,
      latitude: form.latitude ? Number(form.latitude) : undefined,
      longitude: form.longitude ? Number(form.longitude) : undefined,
      radius_km: Number(form.radius_km) || 6,
      service_pincodes: form.service_pincodes.split(",").map((p) => p.trim()).filter(Boolean),
    },
    contact_phone: form.contact_phone || undefined,
    contact_email: form.contact_email || undefined,
    manager_id: form.manager_id || undefined,
    working_hours_start: form.working_hours_start,
    working_hours_end: form.working_hours_end,
  });

  const createMutation = useMutation({
    mutationFn: () => adminServiceCenterApi.create(buildPayload()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-centers"] });
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const updateMutation = useMutation({
    mutationFn: () => adminServiceCenterApi.update(editing!.id, buildPayload()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-centers"] });
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const toggleActiveMutation = useMutation({
    mutationFn: (center: ServiceCenter) => adminServiceCenterApi.update(center.id, { is_active: !center.is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-centers"] }),
  });

  const closeModal = () => {
    setOpen(false);
    setEditing(null);
    setForm(emptyForm);
    setError("");
  };

  const openEdit = (center: ServiceCenter) => {
    setEditing(center);
    setForm({
      name: center.name,
      address: center.location.address,
      city: center.location.city,
      state: center.location.state,
      pincode: center.location.pincode,
      latitude: center.location.latitude != null ? String(center.location.latitude) : "",
      longitude: center.location.longitude != null ? String(center.location.longitude) : "",
      radius_km: String(center.location.radius_km ?? 6),
      service_pincodes: center.location.service_pincodes.join(", "),
      contact_phone: center.contact_phone || "",
      contact_email: center.contact_email || "",
      manager_id: center.manager_id || "",
      working_hours_start: center.working_hours_start || "08:00",
      working_hours_end: center.working_hours_end || "20:00",
    });
    setOpen(true);
  };

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      setError("Geolocation isn't supported on this device.");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        setForm((f) => ({ ...f, latitude: String(pos.coords.latitude), longitude: String(pos.coords.longitude) }));
      },
      (err) => {
        setLocating(false);
        setError(err.message || "Couldn't get your location.");
      },
      { enableHighAccuracy: true, timeout: 15000 }
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Service centers</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Manage regional hubs — coordinates and radius drive automatic booking dispatch.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add service center
        </Button>
      </div>

      <DataTable<ServiceCenter>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No service centers yet"
        columns={[
          { header: "Name", accessor: (c) => c.name },
          { header: "Code", accessor: (c) => <span className="font-mono-num">{c.code}</span> },
          { header: "City", accessor: (c) => c.location.city },
          {
            header: "Coverage",
            accessor: (c) =>
              c.location.latitude != null ? (
                <span className="font-mono-num text-xs">
                  {c.location.latitude.toFixed(3)}, {c.location.longitude?.toFixed(3)} · {c.location.radius_km}km
                </span>
              ) : (
                <span className="text-xs text-[var(--color-text-secondary)]">Pincode only</span>
              ),
          },
          { header: "Status", accessor: (c) => <Badge tone={c.is_active ? "success" : "neutral"}>{c.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (c) => (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => openEdit(c)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={toggleActiveMutation.isPending}
                  onClick={() => toggleActiveMutation.mutate(c)}
                >
                  <Power className="h-3.5 w-3.5" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      <Modal open={open} onClose={closeModal} title={editing ? "Edit service center" : "Add service center"} maxWidth="max-w-xl">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            editing ? updateMutation.mutate() : createMutation.mutate();
          }}
        >
          <Input label="Center name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <Input label="Address" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} required />
          <div className="grid grid-cols-2 gap-3">
            <Input label="City" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} required />
            <Input label="State" value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} required />
          </div>
          <Input label="Pincode" value={form.pincode} onChange={(e) => setForm({ ...form, pincode: e.target.value })} required />

          <div className="rounded-xl border border-dashed border-gray-300 p-4">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-medium text-[var(--color-text-primary)]">Dispatch coordinates</p>
              <Button type="button" size="sm" variant="outline" isLoading={locating} onClick={useMyLocation}>
                <LocateFixed className="h-3.5 w-3.5" /> Use my location
              </Button>
            </div>
            <MapPicker
              latitude={form.latitude ? Number(form.latitude) : null}
              longitude={form.longitude ? Number(form.longitude) : null}
              onChange={(lat, lng) => setForm((f) => ({ ...f, latitude: String(lat), longitude: String(lng) }))}
            />
            <Input
              className="mt-3"
              label="Radius (km)"
              type="number"
              min={1}
              value={form.radius_km}
              onChange={(e) => setForm({ ...form, radius_km: e.target.value })}
            />
            <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
              Bookings within this radius auto-route here by distance; if left blank, dispatch falls back to pincode matching.
            </p>
          </div>

          <Input
            label="Service pincodes (comma separated)"
            value={form.service_pincodes}
            onChange={(e) => setForm({ ...form, service_pincodes: e.target.value })}
            hint="e.g. 452001, 452002, 452010"
            required
          />
          <div className="grid grid-cols-2 gap-3">
            <Input label="Contact phone" value={form.contact_phone} onChange={(e) => setForm({ ...form, contact_phone: e.target.value })} />
            <Input label="Contact email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Opens at" type="time" value={form.working_hours_start} onChange={(e) => setForm({ ...form, working_hours_start: e.target.value })} />
            <Input label="Closes at" type="time" value={form.working_hours_end} onChange={(e) => setForm({ ...form, working_hours_end: e.target.value })} />
          </div>
          <Select label="Manager" value={form.manager_id} onChange={(e) => setForm({ ...form, manager_id: e.target.value })}>
            <option value="">Not assigned</option>
            {(managers?.data || []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.full_name}
              </option>
            ))}
          </Select>

          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createMutation.isPending || updateMutation.isPending}>
            {editing ? "Save changes" : "Add service center"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
