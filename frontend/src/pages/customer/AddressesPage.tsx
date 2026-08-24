import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Plus, Star, Trash2 } from "lucide-react";
import { addressApi } from "../../api/profile";
import { Button, Card, EmptyState, Input, Modal, PageLoader } from "../../components/ui";
import { MapPicker, type ResolvedAddress } from "../../components/shared/MapPicker";
import { getErrorMessage } from "../../lib/api-client";

const emptyForm = {
  label: "Home",
  line1: "",
  line2: "",
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
  const { data: addresses, isLoading } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [detected, setDetected] = useState<ResolvedAddress | null>(null);

  const createMutation = useMutation({
    mutationFn: () =>
      addressApi.create({
        ...form,
        latitude: form.latitude ?? undefined,
        longitude: form.longitude ?? undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["addresses"] });
      setOpen(false);
      setForm(emptyForm);
      setDetected(null);
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

  const handleDelete = (id: string, label: string) => {
    if (!window.confirm(`Remove "${label}"? This can't be undone.`)) return;
    setDeleteError("");
    deleteMutation.mutate(id);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">My addresses</h1>
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
          setDetected(null);
        }}
        title="Add an address"
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
            <MapPicker
              latitude={form.latitude}
              longitude={form.longitude}
              onChange={(lat, lng) => setForm((f) => ({ ...f, latitude: lat, longitude: lng }))}
              onAddressResolved={setDetected}
              showUseMyLocation
            />
            {detected && (
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[var(--color-secondary-light)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                <span>
                  Detected: {detected.line1}
                  {detected.city ? `, ${detected.city}` : ""}
                  {detected.pincode ? ` - ${detected.pincode}` : ""}
                </span>
                <button
                  type="button"
                  className="shrink-0 font-semibold text-[var(--color-primary)]"
                  onClick={() => {
                    setForm((f) => ({
                      ...f,
                      line1: detected.line1 || f.line1,
                      city: detected.city || f.city,
                      state: detected.state || f.state,
                      pincode: detected.pincode || f.pincode,
                    }));
                    setDetected(null);
                  }}
                >
                  Use this
                </button>
              </div>
            )}
          </div>
          <Input label="Label (e.g. Home, Office)" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} required />
          <Input label="Address line 1" value={form.line1} onChange={(e) => setForm({ ...form, line1: e.target.value })} required />
          <Input label="Landmark (optional)" value={form.landmark} onChange={(e) => setForm({ ...form, landmark: e.target.value })} />
          <div className="grid grid-cols-2 gap-3">
            <Input label="City" value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} required />
            <Input label="State" value={form.state} onChange={(e) => setForm({ ...form, state: e.target.value })} required />
          </div>
          <Input label="Pincode" value={form.pincode} onChange={(e) => setForm({ ...form, pincode: e.target.value })} required />
          <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            <input type="checkbox" checked={form.is_default} onChange={(e) => setForm({ ...form, is_default: e.target.checked })} />
            Set as default address
          </label>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createMutation.isPending}>
            Add address
          </Button>
        </form>
      </Modal>
    </div>
  );
}
