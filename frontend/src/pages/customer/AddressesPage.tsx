import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Pencil, Plus, Trash2 } from "lucide-react";
import { addressApi } from "../../api/profile";
import { Badge, Button, Card, EmptyState, Input, Modal, PageLoader } from "../../components/ui";
import { LocationPicker, type LocationValue } from "../../components/shared/LocationPicker";
import { getErrorMessage } from "../../lib/api-client";
import { useConfirm } from "../../context/ConfirmContext";
import { PageHeader } from "../../components/customer/ui";

const emptyForm = {
  label: "Home",
  line1: "",
  landmark: "",
  city: "",
  state: "",
  pincode: "",
  latitude: null as number | null,
  longitude: null as number | null,
  is_default: false,
};

export default function AddressesPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { data: addresses, isLoading } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list });
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [location, setLocation] = useState<LocationValue | null>(null);
  const [mapsDown, setMapsDown] = useState(false);
  const [error, setError] = useState("");
  const [deleteError, setDeleteError] = useState("");

  const createMutation = useMutation({
    mutationFn: () => {
      const payload = {
        ...form,
        line1: location ? [form.line1, location.area, location.city].filter(Boolean).join(", ") : form.line1,
        latitude: form.latitude ?? undefined,
        longitude: form.longitude ?? undefined,
      };
      // Edit = same form, PUT — no more delete-and-re-add to fix a typo.
      return editingId ? addressApi.update(editingId, payload) : addressApi.create(payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["addresses"] });
      setOpen(false);
      setEditingId(null);
      setForm(emptyForm);
      setLocation(null);
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: addressApi.remove,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["addresses"] });
      setDeleteError("");
    },
    // e.g. the backend now refuses to delete an address an active booking
    // still references — surfaced here rather than silently doing nothing.
    onError: (err) => setDeleteError(getErrorMessage(err)),
  });

  const handleDelete = async (id: string, label: string) => {
    if (!(await confirm({ title: `Remove "${label}"?`, message: "This can't be undone.", tone: "danger" }))) return;
    setDeleteError("");
    deleteMutation.mutate(id);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        back="/app/settings"
        title="My Addresses"
        right={
          <Button variant="info" onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" />
            <span className="sm:hidden">Add</span>
            <span className="hidden sm:inline">Add Address</span>
          </Button>
        }
      />

      {deleteError && <p className="text-sm text-[var(--color-error)]">{deleteError}</p>}

      {isLoading ? (
        <PageLoader />
      ) : !addresses?.length ? (
        <EmptyState icon={MapPin} title="No Addresses Yet" action={<Button variant="info" onClick={() => setOpen(true)}>Add Address</Button>} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {addresses.map((a) => (
            <Card key={a.id} className="p-5">
              <div className="flex items-start justify-between">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0E1A33]">
                  <MapPin className="h-5 w-5" />
                </span>
                <div className="flex items-center gap-2">
                  {a.is_default && <Badge>Default</Badge>}
                  <button
                    title="Edit"
                    onClick={() => {
                      setEditingId(a.id);
                      setLocation(null);
                      setForm({
                        label: a.label,
                        line1: a.line1,
                        landmark: a.landmark || "",
                        city: a.city,
                        state: a.state,
                        pincode: a.pincode,
                        latitude: a.latitude ?? null,
                        longitude: a.longitude ?? null,
                        is_default: !!a.is_default,
                      });
                      setError("");
                      setOpen(true);
                    }}
                    className="text-gray-400 hover:text-[#0E1A33]"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button onClick={() => handleDelete(a.id, a.label)} className="text-gray-400 hover:text-[var(--color-error)]">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <h3 className="mt-3 font-semibold text-[#0E1A33]">{a.label}</h3>
              <p className="mt-0.5 text-sm text-gray-600">
                {a.line1}, {a.city}, {a.state} - {a.pincode}
              </p>
              {a.latitude == null && (
                <p className="mt-1.5 text-xs text-amber-700">No map pin — edit to add one.</p>
              )}
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={open}
        onClose={() => {
          setOpen(false);
          setEditingId(null);
          setForm(emptyForm);
          setLocation(null);
        }}
        title={editingId ? "Edit Address" : "Add An Address"}
        maxWidth="max-w-xl"
      >
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            createMutation.mutate();
          }}
        >
          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Pin Your Exact Location</p>
            <LocationPicker
              value={location}
              onUnavailable={() => setMapsDown(true)}
              onChange={(v) => {
                setLocation(v);
                setForm((f) => ({
                  ...f,
                  latitude: v.latitude,
                  longitude: v.longitude,
                  city: v.city || "Indore",
                  state: v.state || "Madhya Pradesh",
                  pincode: v.pincode || f.pincode,
                }));
              }}
            />
          </div>
          <Input label="Label (e.g. Home, Office)" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} required />
          <Input label="House / Flat, Gali No." placeholder="e.g. 75, Gali No. 2" value={form.line1} onChange={(e) => setForm({ ...form, line1: e.target.value })} required />
          <Input label="Landmark (Optional)" value={form.landmark} onChange={(e) => setForm({ ...form, landmark: e.target.value })} />
          {mapsDown && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Input label="City" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} required />
                <Input label="State" value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} required />
              </div>
              <Input label="Pincode" value={form.pincode} onChange={(e) => setForm({ ...form, pincode: e.target.value })} required />
            </>
          )}
          <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            <input type="checkbox" checked={form.is_default} onChange={(e) => setForm({ ...form, is_default: e.target.checked })} />
            Set As Default Address
          </label>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          {!mapsDown && form.latitude == null && (
            <p className="text-xs text-amber-700">Pin your location on the map first.</p>
          )}
          <Button type="submit" variant="info" className="w-full" disabled={!editingId && !mapsDown && form.latitude == null} isLoading={createMutation.isPending}>
            {editingId ? "Save Changes" : "Add Address"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
