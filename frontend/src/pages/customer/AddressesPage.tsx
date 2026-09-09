import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Pencil, Plus, Star, Trash2 } from "lucide-react";
import { addressApi } from "../../api/profile";
import { Button, Card, EmptyState, Input, Modal, PageLoader } from "../../components/ui";
import { LocationPicker, type LocationValue } from "../../components/shared/LocationPicker";
import { getErrorMessage } from "../../lib/api-client";
import { useConfirm } from "../../context/ConfirmContext";

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
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">Locations</p>
          <h1 className="mt-1 font-display text-2xl font-bold text-[var(--color-text-primary)]">My addresses</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Where should our captains meet you?</p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add address
        </Button>
      </div>

      {deleteError && <p className="text-sm text-[var(--color-error)]">{deleteError}</p>}

      {isLoading ? (
        <PageLoader />
      ) : !addresses?.length ? (
        <EmptyState icon={MapPin} title="No addresses added" description="Add an address to book your first service." action={<Button onClick={() => setOpen(true)}>Add address</Button>} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {addresses.map((a) => (
            <Card key={a.id} className="p-5">
              <div className="flex items-start justify-between">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                  <MapPin className="h-5 w-5" />
                </span>
                <div className="flex items-center gap-2">
                  {a.is_default && <Star className="h-4 w-4 fill-amber-400 text-amber-400" />}
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
                    className="text-gray-400 hover:text-[var(--color-primary)]"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button onClick={() => handleDelete(a.id, a.label)} className="text-gray-400 hover:text-[var(--color-error)]">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <h3 className="mt-3 font-semibold text-[var(--color-text-primary)]">{a.label}</h3>
              <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">
                {a.line1}, {a.city}, {a.state} - {a.pincode}
              </p>
              {a.latitude == null && (
                <p className="mt-1.5 text-xs text-[var(--color-warning)]">No pinned location — dispatch will fall back to pincode matching.</p>
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
        title={editingId ? "Edit address" : "Add an address"}
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
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Pin your exact location</p>
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
          <Input label="House / flat, gali no." placeholder="e.g. 75, Gali No. 2" value={form.line1} onChange={(e) => setForm({ ...form, line1: e.target.value })} required />
          <Input label="Landmark (optional)" value={form.landmark} onChange={(e) => setForm({ ...form, landmark: e.target.value })} />
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
            Set as default address
          </label>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          {!mapsDown && form.latitude == null && (
            <p className="text-xs text-amber-700">Set your location on the map first — the captain navigates to that exact pin.</p>
          )}
          <Button type="submit" className="w-full" disabled={!editingId && !mapsDown && form.latitude == null} isLoading={createMutation.isPending}>
            {editingId ? "Save changes" : "Add address"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
