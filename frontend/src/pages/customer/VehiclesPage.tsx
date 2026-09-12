import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Car, Pencil, Plus, Star, Trash2 } from "lucide-react";
import { vehicleApi } from "../../api/profile";
import { vehicleTypeApi } from "../../api/catalog";
import { Button, Card, EmptyState, Input, Modal, PageLoader, Select } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { PLATE_FORMAT_HINT, validateIndianPlate } from "../../lib/validators";
import { useConfirm } from "../../context/ConfirmContext";
import { VehicleIcon } from "../../components/shared/VehicleIcon";

const emptyForm = { vehicle_type: "", brand: "", model: "", registration_number: "", color: "", is_default: false };

export default function VehiclesPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { data: vehicles, isLoading } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [sharedRegOpen, setSharedRegOpen] = useState(false);
  const [checkingRegistration, setCheckingRegistration] = useState(false);

  // Default the form to the first admin-configured type once they've loaded
  // — there's no more hardcoded "car" to fall back to.
  useEffect(() => {
    if (vehicleTypes?.length && !form.vehicle_type) {
      setForm((f) => ({ ...f, vehicle_type: vehicleTypes[0].id }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleTypes]);

  const typeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || "Vehicle";

  const createMutation = useMutation({
    // Fix-a-typo flow: same form, PUT instead of POST when editing — the
    // old page forced delete-and-re-add for any correction.
    mutationFn: (acknowledge?: boolean) =>
      editingId
        ? vehicleApi.update(editingId, form)
        : vehicleApi.create({ ...form, acknowledge_shared_registration: acknowledge }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vehicles"] });
      setOpen(false);
      setSharedRegOpen(false);
      setEditingId(null);
      setForm((f) => ({ ...emptyForm, vehicle_type: f.vehicle_type }));
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: vehicleApi.remove,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vehicles"] });
      setDeleteError("");
    },
    // e.g. the backend now refuses to delete a vehicle an active booking
    // still references — surfaced here rather than silently doing nothing.
    onError: (err) => setDeleteError(getErrorMessage(err)),
  });

  const handleDelete = async (id: string, label: string) => {
    if (!(await confirm({ title: `Remove ${label}?`, message: "This can't be undone.", tone: "danger" }))) return;
    setDeleteError("");
    deleteMutation.mutate(id);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!validateIndianPlate(form.registration_number)) {
      setError(`That doesn't look like a valid registration number — ${PLATE_FORMAT_HINT}`);
      return;
    }
    if (editingId) {
      createMutation.mutate(undefined);
      return;
    }
    setCheckingRegistration(true);
    try {
      const check = await vehicleApi.checkRegistration(form.registration_number);
      setCheckingRegistration(false);
      if (check.already_registered) {
        setSharedRegOpen(true);
        return;
      }
      createMutation.mutate(undefined);
    } catch (err) {
      setCheckingRegistration(false);
      setError(getErrorMessage(err));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">Garage</p>
          <h1 className="mt-1 font-display text-2xl font-bold text-[var(--color-text-primary)]">My vehicles</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Manage the vehicles you book services for.</p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add vehicle
        </Button>
      </div>

      {deleteError && <p className="text-sm text-[var(--color-error)]">{deleteError}</p>}

      {isLoading ? (
        <PageLoader />
      ) : !vehicles?.length ? (
        <EmptyState icon={Car} title="No vehicles added" description="Add a vehicle to start booking services." action={<Button onClick={() => setOpen(true)}>Add vehicle</Button>} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {vehicles.map((v) => (
            <Card key={v.id} className="p-5">
              <div className="flex items-start justify-between">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                  <VehicleIcon vehicleTypeId={v.vehicle_type} className="h-5 w-5" />
                </span>
                <div className="flex items-center gap-2">
                  {v.is_default && <Star className="h-4 w-4 fill-amber-400 text-amber-400" />}
                  <button
                    title="Edit"
                    onClick={() => {
                      setEditingId(v.id);
                      setForm({
                        vehicle_type: v.vehicle_type,
                        brand: v.brand,
                        model: v.model,
                        registration_number: v.registration_number,
                        color: v.color || "",
                        is_default: !!v.is_default,
                      });
                      setError("");
                      setOpen(true);
                    }}
                    className="text-gray-400 hover:text-[var(--color-primary)]"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => handleDelete(v.id, `${v.brand} ${v.model} (${v.registration_number})`)}
                    className="text-gray-400 hover:text-[var(--color-error)]"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <h3 className="mt-3 font-semibold text-[var(--color-text-primary)]">
                {v.brand} {v.model}
              </h3>
              <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">
                {typeName(v.vehicle_type)} · {v.registration_number}
              </p>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={open}
        onClose={() => {
          setOpen(false);
          setEditingId(null);
          setForm((f) => ({ ...emptyForm, vehicle_type: f.vehicle_type }));
        }}
        title={editingId ? "Edit vehicle" : "Add a vehicle"}
      >
        <form className="space-y-4" onSubmit={handleSubmit}>
          <Select label="Vehicle type" value={form.vehicle_type} onChange={(e) => setForm({ ...form, vehicle_type: e.target.value })}>
            {(vehicleTypes || []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Brand" value={form.brand} onChange={(e) => setForm({ ...form, brand: e.target.value })} required />
            <Input label="Model" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} required />
          </div>
          <Input
            label="Registration number"
            value={form.registration_number}
            onChange={(e) => setForm({ ...form, registration_number: e.target.value })}
            hint={PLATE_FORMAT_HINT}
            required
          />
          <Input label="Color (optional)" value={form.color} onChange={(e) => setForm({ ...form, color: e.target.value })} />
          <label className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
            <input type="checkbox" checked={form.is_default} onChange={(e) => setForm({ ...form, is_default: e.target.checked })} />
            Set as default vehicle
          </label>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={checkingRegistration || createMutation.isPending}>
            {editingId ? "Save changes" : "Add vehicle"}
          </Button>
        </form>
      </Modal>

      <Modal open={sharedRegOpen} onClose={() => setSharedRegOpen(false)} title="Already registered elsewhere">
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            This vehicle is already registered with another account. You can continue using this vehicle from that account, or
            continue adding it to this account.
          </p>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-3">
            <Button type="button" variant="outline" className="flex-1" onClick={() => setSharedRegOpen(false)}>
              Cancel
            </Button>
            <Button type="button" className="flex-1" isLoading={createMutation.isPending} onClick={() => createMutation.mutate(true)}>
              Continue
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
