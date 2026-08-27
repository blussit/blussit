import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RotateCcw, Trash2, UserX } from "lucide-react";
import { adminUserApi, adminServiceCenterApi } from "../../api/admin";
import { Button, DataTable, Input, Modal, Select, StatusBadge } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import type { User, UserRole } from "../../types";

const emptyForm = { full_name: "", email: "", phone: "", password: "", role: "captain" as UserRole, service_center_id: "" };

export default function AdminUsersPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [role, setRole] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["admin-users", role, page],
    queryFn: () => adminUserApi.list({ role: role || undefined, page, page_size: 15 }),
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

      <div className="max-w-xs">
        <Select label="Filter by role" value={role} onChange={(e) => { setRole(e.target.value); setPage(1); }}>
          <option value="">All roles</option>
          <option value="customer">Customer</option>
          <option value="captain">Captain</option>
          <option value="manager">Manager</option>
          <option value="admin">Admin</option>
        </Select>
      </div>

      <DataTable<User>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No users found"
        columns={[
          { header: "Name", accessor: (u) => u.full_name },
          { header: "Contact", accessor: (u) => u.email || u.phone || "—" },
          { header: "Role", accessor: (u) => <span className="capitalize">{u.role}</span> },
          { header: "Status", accessor: (u) => <StatusBadge status={u.status} /> },
          {
            header: "",
            accessor: (u) => (
              <div className="flex gap-2">
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
    </div>
  );
}
