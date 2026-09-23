import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, RotateCcw, Trash2, UserX } from "lucide-react";
import { adminUserApi, adminServiceCenterApi } from "../../api/admin";
import { Button, DataTable, Input, Modal, Select, StatusBadge } from "../../components/ui";
import { CustomerDetailDrawer } from "../../components/shared/CustomerDetailDrawer";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import type { User, UserRole } from "../../types";

/** A pasted "+91 98765 43210" should still find the stored 10-digit number. */
function normaliseSearch(raw: string): string {
  const text = raw.trim();
  if (!/^[+\d][\d\s+-]*$/.test(text)) return text;
  let digits = text.replace(/\D/g, "");
  if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
  return digits;
}

const emptyForm = { full_name: "", email: "", phone: "", password: "", role: "captain" as UserRole, service_center_id: "" };

export default function AdminUsersPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [role, setRole] = useState("");
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  // Editing an EXISTING account — most often to link/relink a manager's
  // service center. adminUserApi.update (PUT /users/:id) already existed
  // and already supported this field; it just had no UI to reach it, which
  // is how a manager account with no center could exist with no way to fix
  // it short of a raw API call — a real incident this closes.
  const [editUser, setEditUser] = useState<User | null>(null);
  const [editForm, setEditForm] = useState({ full_name: "", role: "captain" as UserRole, service_center_id: "" });
  const [editError, setEditError] = useState("");
  // A customer's name opens their full purchase history (bookings + plans);
  // get_customer_360 is customer-only, so staff rows don't get this.
  const [detailCustomerId, setDetailCustomerId] = useState<string | null>(null);

  // Search runs on the server (name / phone / email across every page), a
  // beat after the last keystroke so typing never fires a request per letter.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced(normaliseSearch(search));
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["admin-users", role, debounced, page],
    queryFn: () => adminUserApi.list({ role: role || undefined, search: debounced || undefined, page, page_size: 15 }),
    placeholderData: keepPreviousData, // the old rows stay put while the new ones load — no flash
  });

  const { data: centers } = useQuery({ queryKey: ["admin-centers-lite"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });

  const createStaffMutation = useMutation({
    mutationFn: () => adminUserApi.createStaff(form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      setOpen(false);
      setForm(emptyForm);
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const suspendMutation = useMutation({
    mutationFn: adminUserApi.suspend,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });

  const reactivateMutation = useMutation({
    mutationFn: (id: string) => adminUserApi.update(id, { status: "active" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminUserApi.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });

  const updateMutation = useMutation({
    mutationFn: () => adminUserApi.update(editUser!.id, { ...editForm, service_center_id: editForm.service_center_id || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      setEditUser(null);
    },
    onError: (err) => setEditError(getErrorMessage(err)),
  });

  const openEdit = (u: User) => {
    setEditError("");
    setEditForm({ full_name: u.full_name, role: u.role, service_center_id: u.service_center_id || "" });
    setEditUser(u);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Users</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Manage customers, captains, and managers.</p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add staff account
        </Button>
      </div>

      <div className="grid grid-cols-1 items-end gap-3 sm:flex sm:flex-wrap">
        <div className="sm:min-w-[260px] sm:max-w-md sm:flex-1">
          <Input
            label="Search"
            type="search"
            placeholder="Name, phone or email"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoComplete="off"
          />
        </div>
        <div className="sm:w-56">
          <Select label="Filter by role" value={role} onChange={(e) => { setRole(e.target.value); setPage(1); }}>
            <option value="">All roles</option>
            <option value="customer">Customer</option>
            <option value="captain">Captain</option>
            <option value="manager">Manager</option>
            <option value="admin">Admin</option>
          </Select>
        </div>
        {data && (
          <p className={`pb-2.5 text-sm text-[var(--color-text-secondary)] transition-opacity ${isFetching ? "opacity-50" : ""}`}>
            {data.meta.total} {data.meta.total === 1 ? "user" : "users"}
            {debounced ? ` matching “${search.trim()}”` : ""}
          </p>
        )}
      </div>

      <DataTable<User>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle={debounced ? `No users match “${search.trim()}”` : "No users found"}
        columns={[
          {
            header: "Name",
            accessor: (u) =>
              u.role === "customer" ? (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setDetailCustomerId(u.id);
                  }}
                  className="font-medium text-black underline decoration-[#F3E5B5] decoration-2 underline-offset-2 hover:decoration-black"
                >
                  {u.full_name}
                </button>
              ) : (
                u.full_name
              ),
          },
          { header: "Contact", accessor: (u) => u.email || u.phone || "—" },
          { header: "Role", accessor: (u) => <span className="capitalize">{u.role}</span> },
          { header: "Status", accessor: (u) => <StatusBadge status={u.status} /> },
          {
            header: "",
            accessor: (u) => (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => openEdit(u)}>
                  <Pencil className="h-3.5 w-3.5" /> Edit
                </Button>
                {u.status !== "suspended" ? (
                  <Button size="sm" variant="outline" isLoading={suspendMutation.isPending} onClick={() => suspendMutation.mutate(u.id)}>
                    <UserX className="h-3.5 w-3.5" /> Suspend
                  </Button>
                ) : (
                  <Button size="sm" variant="outline" isLoading={reactivateMutation.isPending} onClick={() => reactivateMutation.mutate(u.id)}>
                    <RotateCcw className="h-3.5 w-3.5" /> Reactivate
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={deleteMutation.isPending}
                  onClick={async () => {
                    if (await confirm({ title: `Delete ${u.full_name}?`, message: "This cannot be undone.", tone: "danger" })) deleteMutation.mutate(u.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      {data && data.meta.total_pages > 1 && (
        <div className="flex justify-center gap-2">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button size="sm" variant="outline" disabled={page >= data.meta.total_pages} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Add staff account">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            createStaffMutation.mutate();
          }}
        >
          <Input label="Full name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
          <Input label="Email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <Input label="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <Input label="Temporary password" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
          <Select label="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as UserRole })}>
            <option value="captain">Captain</option>
            <option value="manager">Manager</option>
            <option value="admin">Admin</option>
          </Select>
          <Select label="Service center" value={form.service_center_id} onChange={(e) => setForm({ ...form, service_center_id: e.target.value })}>
            <option value="">Not assigned</option>
            {(centers?.data || []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createStaffMutation.isPending}>
            Create account
          </Button>
        </form>
      </Modal>

      <Modal open={!!editUser} onClose={() => setEditUser(null)} title={`Edit ${editUser?.full_name || "user"}`}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setEditError("");
            updateMutation.mutate();
          }}
        >
          <Input label="Full name" value={editForm.full_name} onChange={(e) => setEditForm({ ...editForm, full_name: e.target.value })} required />
          {editUser && editUser.role !== "customer" && (
            <>
              <Select label="Role" value={editForm.role} onChange={(e) => setEditForm({ ...editForm, role: e.target.value as UserRole })}>
                <option value="captain">Captain</option>
                <option value="manager">Manager</option>
                <option value="admin">Admin</option>
              </Select>
              <Select
                label="Service center"
                value={editForm.service_center_id}
                onChange={(e) => setEditForm({ ...editForm, service_center_id: e.target.value })}
              >
                <option value="">Not assigned</option>
                {(centers?.data || []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
              {editForm.role === "manager" && !editForm.service_center_id && (
                <p className="text-xs text-amber-600">
                  A manager with no center can't sell/assign plans, or see their own Subscriptions or KPI pages.
                </p>
              )}
            </>
          )}
          {editError && <p className="text-sm text-[var(--color-error)]">{editError}</p>}
          <Button type="submit" className="w-full" isLoading={updateMutation.isPending}>
            Save changes
          </Button>
        </form>
      </Modal>

      <CustomerDetailDrawer customerId={detailCustomerId} onClose={() => setDetailCustomerId(null)} />
    </div>
  );
}
