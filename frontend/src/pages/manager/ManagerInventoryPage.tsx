import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Package, Pencil, Plus, Trash2 } from "lucide-react";
import { inventoryApi } from "../../api/admin";
import { Badge, Button, Card, EmptyState, Input, Modal, PageLoader, Select } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import type { InventoryItem } from "../../types";

const emptyForm = { item_name: "", unit: "litre", quantity_available: 0, reorder_level: 0 };

export default function ManagerInventoryPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<InventoryItem | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["center-inventory", centerId],
    queryFn: () => inventoryApi.forCenter(centerId, { page: 1, page_size: 50 }),
    enabled: !!centerId,
  });
  const items = data?.data || [];

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["center-inventory"] });
  const closeModal = () => {
    setOpen(false);
    setEditing(null);
    setForm(emptyForm);
    setError("");
  };

  const createMutation = useMutation({
    mutationFn: () => inventoryApi.create({ ...form, service_center_id: centerId }),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const updateMutation = useMutation({
    mutationFn: () => inventoryApi.update(editing!.id, form),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => inventoryApi.remove(id),
    onSuccess: invalidate,
  });

  const openEdit = (item: InventoryItem) => {
    setEditing(item);
    setForm({ item_name: item.item_name, unit: item.unit, quantity_available: item.quantity_available, reorder_level: item.reorder_level });
    setOpen(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Inventory</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Track consumables at your service center.</p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add item
        </Button>
      </div>

      {isLoading ? (
        <PageLoader />
      ) : !items.length ? (
        <EmptyState icon={Package} title="No inventory items yet" action={<Button onClick={() => setOpen(true)}>Add item</Button>} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((i) => {
            const lowStock = i.quantity_available <= i.reorder_level;
            return (
              <Card key={i.id} className="p-5">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                      <Package className="h-5 w-5" />
                    </span>
                    <div>
                      <p className="font-semibold text-[var(--color-text-primary)]">{i.item_name}</p>
                      <p className="text-xs capitalize text-[var(--color-text-secondary)]">{i.unit}</p>
                    </div>
                  </div>
                  {lowStock && <Badge tone="warning">Low stock</Badge>}
                </div>

                <div className="mt-4 flex items-baseline gap-1.5">
                  <span className="font-mono-num text-3xl font-bold text-[var(--color-text-primary)]">{i.quantity_available}</span>
                  <span className="text-sm text-[var(--color-text-secondary)]">{i.unit} on hand</span>
                </div>
                <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Reorder level: {i.reorder_level}</p>

                <div className="mt-4 flex gap-2">
                  <Button size="sm" variant="outline" className="flex-1" onClick={() => openEdit(i)}>
                    <Pencil className="h-3.5 w-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    isLoading={deleteMutation.isPending}
                    onClick={async () => {
                      if (await confirm({ title: `Delete "${i.item_name}" from inventory?`, tone: "danger" })) deleteMutation.mutate(i.id);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Modal open={open} onClose={closeModal} title={editing ? "Edit inventory item" : "Add inventory item"}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            editing ? updateMutation.mutate() : createMutation.mutate();
          }}
        >
          <Input label="Item name" value={form.item_name} onChange={(e) => setForm({ ...form, item_name: e.target.value })} required />
          <Select label="Unit" value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })}>
            <option value="litre">Litre</option>
            <option value="ml">ML</option>
            <option value="piece">Piece</option>
            <option value="kg">Kg</option>
            <option value="gram">Gram</option>
            <option value="bottle">Bottle</option>
          </Select>
          <div className="grid grid-cols-2 gap-3">
            <Input
              label={editing ? "Quantity on hand" : "Starting quantity"}
              type="number"
              value={form.quantity_available}
              onChange={(e) => setForm({ ...form, quantity_available: Number(e.target.value) })}
              required
              hint={editing ? "Update this to however much is on hand now — e.g. after restocking." : undefined}
            />
            <Input
              label="Reorder level"
              type="number"
              value={form.reorder_level}
              onChange={(e) => setForm({ ...form, reorder_level: Number(e.target.value) })}
            />
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createMutation.isPending || updateMutation.isPending}>
            {editing ? "Save changes" : "Add item"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
