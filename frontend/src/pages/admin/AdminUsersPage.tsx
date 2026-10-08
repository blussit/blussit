import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Plus, RotateCcw, Trash2, UserX } from "lucide-react";
import { adminUserApi, adminServiceCenterApi } from "../../api/admin";
import { Button, DataTable, ErrorState, Input, Modal, Select, StatusBadge } from "../../components/ui";
import { CustomerDetailDrawer } from "../../components/shared/CustomerDetailDrawer";
import { StaffResetPasswordDialog } from "../../components/shared/StaffResetPasswordDialog";
import { ManagerAlertsToggle } from "../../components/manager/WhatsAppAlertsSwitch";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { cleanMobileInput, validateIndianMobile } from "../../lib/validators";
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
  const { push: pushToast } = useToast();
  const { user: me } = useAuth();
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
  const [editForm, setEditForm] = useState({ full_name: "", role: "captain" as UserRole, service_center_id: "", phone: "" });
  const [editError, setEditError] = useState("");
  // A customer's name opens their full purchase history (bookings + plans);
  // get_customer_360 is customer-only, so staff rows don't get this.
  const [detailCustomerId, setDetailCustomerId] = useState<string | null>(null);
  const [resetFor, setResetFor] = useState<User | null>(null);

  // Search runs on the server (name / phone / email across every page), a
  // beat after the last keystroke so typing never fires a request per letter.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced(normaliseSearch(search));
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const { data, isLoading, isFetching, error: usersError, refetch: refetchUsers } = useQuery({
    queryKey: ["admin-users", role, debounced, page],
    queryFn: () => adminUserApi.list({ role: role || undefined, search: debounced || undefined, page, page_size: 15 }),
    placeholderData: keepPreviousData, // the old rows stay put while the new ones load — no flash
  });

  const centersQuery = useQuery({ queryKey: ["admin-centers-lite"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });
  const centers = centersQuery.data;
  // A failed centers read would leave the picker on "Not Assigned" only —
  // which reads as "this person has no center". Say it failed instead.
  const centersError =
    centersQuery.isError && !centers ? (
      <ErrorState message="Couldn't load service centers." onRetry={() => void centersQuery.refetch()} busy={centersQuery.isFetching} className="p-4" />
    ) : null;

  const createStaffMutation = useMutation({
    // Blank optional fields go as "absent", never "" — an empty email was a
    // 422 ("not a valid email address"), so a phone-only captain could
    // never be created from here.
    mutationFn: () =>
      adminUserApi.createStaff({
        full_name: form.full_name.trim(),
        password: form.password,
        role: form.role,
        email: form.email.trim().toLowerCase() || undefined,
        phone: form.phone || undefined,
        service_center_id: form.service_center_id || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      setOpen(false);
      setForm(emptyForm);
      pushToast({ tone: "success", title: "Staff account created" });
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  // Every row action reports back — a refused suspend/delete (the only
  // admin, an account with live bookings) used to fail in total silence.
  const rowError = (err: unknown) => pushToast({ tone: "error", title: getErrorMessage(err) });
  const suspendMutation = useMutation({
    mutationFn: adminUserApi.suspend,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      pushToast({ tone: "success", title: "Account suspended — signed out everywhere" });
    },
    onError: rowError,
  });

  const reactivateMutation = useMutation({
    mutationFn: (id: string) => adminUserApi.update(id, { status: "active" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      pushToast({ tone: "success", title: "Account reactivated" });
    },
    onError: rowError,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminUserApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      pushToast({ tone: "success", title: "Account deleted" });
    },
    onError: rowError,
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      const { phone, ...rest } = editForm;
      // Phone goes only when it actually changed — the backend then marks
      // it unverified and blocks duplicates / our own WhatsApp number.
      const phoneChanged = phone !== (editUser!.phone || "");
      return adminUserApi.update(editUser!.id, {
        ...rest,
        service_center_id: editForm.service_center_id || null,
        ...(phoneChanged ? { phone } : {}),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      setEditUser(null);
      pushToast({ tone: "success", title: "Account updated" });
    },
    onError: (err) => setEditError(getErrorMessage(err)),
  });

  const openEdit = (u: User) => {
    setEditError("");
    setEditForm({ full_name: u.full_name, role: u.role, service_center_id: u.service_center_id || "", phone: u.phone || "" });
    setEditUser(u);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Users</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Manage customers, captains, and managers.</p>
        </div>
        <Button
          onClick={() => {
            setError("");
            setForm(emptyForm);
            setOpen(true);
          }}
        >
          <Plus className="h-4 w-4" /> Add Staff Account
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
          <Select label="Filter By Role" value={role} onChange={(e) => { setRole(e.target.value); setPage(1); }}>
            <option value="">All Roles</option>
            <option value="customer">Customer</option>
            <option value="captain">Captain</option>
            <option value="manager">Manager</option>
            <option value="admin">Admin</option>
          </Select>
        </div>
        {data && (
          <p className={`pb-2.5 text-sm text-[var(--color-text-secondary)] transition-opacity ${isFetching ? "opacity-50" : ""}`}>
            {data.meta.total} {data.meta.total === 1 ? "User" : "Users"}
            {debounced ? ` Matching “${search.trim()}”` : ""}
          </p>
        )}
      </div>

      <DataTable<User>
        isLoading={isLoading}
        data={data?.data || []}
        error={usersError}
        onRetry={() => void refetchUsers()}
        emptyTitle={debounced ? `No Users Match “${search.trim()}”` : "No Users Found"}
        onRowClick={(u) => (u.role === "customer" ? setDetailCustomerId(u.id) : openEdit(u))}
        columns={[
          { header: "Name", accessor: (u) => <span className="font-medium text-black">{u.full_name}</span> },
          { header: "Contact", accessor: (u) => u.email || u.phone || "—" },
          { header: "Role", accessor: (u) => <span className="capitalize">{u.role}</span> },
          { header: "Status", accessor: (u) => <StatusBadge status={u.status} /> },
          ...(role === "" || role === "manager"
            ? [
                {
                  header: "WhatsApp Alerts",
                  accessor: (u: User) =>
                    u.role === "manager" ? (
                      <ManagerAlertsToggle userId={u.id} enabled={u.whatsapp_new_booking_alerts !== false} name={u.full_name} />
                    ) : (
                      <span className="text-gray-300">—</span>
                    ),
                },
              ]
            : []),
          {
            header: "",
            accessor: (u) => (
              <div className="flex justify-end gap-2 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                <Button size="sm" variant="outline" onClick={() => openEdit(u)}>
                  <Pencil className="h-3.5 w-3.5" /> Edit
                </Button>
                {u.role !== "customer" && u.id !== me?.id && (
                  <Button size="sm" variant="outline" onClick={() => setResetFor(u)}>
                    <KeyRound className="h-3.5 w-3.5" /> Reset Password
                  </Button>
                )}
                {u.id === me?.id ? null : u.status !== "suspended" ? (
                  <Button
                    size="sm"
                    variant="outline"
                    isLoading={suspendMutation.isPending && suspendMutation.variables === u.id}
                    onClick={async () => {
                      if (
                        await confirm({
                          title: `Suspend ${u.full_name}?`,
                          message: "They're signed out on every device and can't log in until reactivated.",
                          tone: "danger",
                          confirmLabel: "Suspend",
                        })
                      )
                        suspendMutation.mutate(u.id);
                    }}
                  >
                    <UserX className="h-3.5 w-3.5" /> Suspend
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    isLoading={reactivateMutation.isPending && reactivateMutation.variables === u.id}
                    onClick={() => reactivateMutation.mutate(u.id)}
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> Reactivate
                  </Button>
                )}
                {u.id !== me?.id && (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Delete ${u.full_name}`}
                    title="Delete account"
                    isLoading={deleteMutation.isPending && deleteMutation.variables === u.id}
                    onClick={async () => {
                      if (await confirm({ title: `Delete ${u.full_name}?`, message: "This cannot be undone.", tone: "danger", confirmLabel: "Delete" }))
                        deleteMutation.mutate(u.id);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                  </Button>
                )}
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

      <Modal open={open} onClose={() => setOpen(false)} title="Add Staff Account">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            if (form.phone && !validateIndianMobile(form.phone)) {
              setError("Enter a valid 10-digit mobile number.");
              return;
            }
            if (!form.email && !form.phone) {
              setError("Add an email or a phone number — it's how they sign in.");
              return;
            }
            if (form.role === "manager" && !form.service_center_id) {
              setError("Pick the service center this manager runs.");
              return;
            }
            createStaffMutation.mutate();
          }}
        >
          <Input label="Full Name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
          <Input label="Email" type="email" autoCapitalize="none" autoCorrect="off" spellCheck={false} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value.toLowerCase() })} />
          <Input
            label="Phone"
            type="tel"
            inputMode="numeric"
            placeholder="10-digit mobile"
            value={form.phone}
            onChange={(e) => setForm({ ...form, phone: cleanMobileInput(e.target.value) })}
          />
          <Input
            label="Temporary Password"
            type="password"
            minLength={8}
            autoComplete="new-password"
            hint="At least 8 characters. Share it privately — they must set their own password on first login."
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
          />
          <Select label="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as UserRole })}>
            <option value="captain">Captain</option>
            <option value="manager">Manager</option>
            <option value="admin">Admin</option>
          </Select>
          <Select label="Service Center" value={form.service_center_id} onChange={(e) => setForm({ ...form, service_center_id: e.target.value })}>
            <option value="">Not Assigned</option>
            {(centers?.data || []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          {centersError}
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createStaffMutation.isPending}>
            Create Account
          </Button>
        </form>
      </Modal>

      <Modal open={!!editUser} onClose={() => setEditUser(null)} title={`Edit ${editUser?.full_name || "User"}`}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setEditError("");
            if (editForm.phone !== (editUser?.phone || "") && !validateIndianMobile(editForm.phone)) {
              setEditError("Enter a valid 10-digit mobile number.");
              return;
            }
            updateMutation.mutate();
          }}
        >
          <Input label="Full Name" value={editForm.full_name} onChange={(e) => setEditForm({ ...editForm, full_name: e.target.value })} required />
          <Input
            label="Phone"
            type="tel"
            inputMode="numeric"
            value={editForm.phone}
            onChange={(e) => setEditForm({ ...editForm, phone: cleanMobileInput(e.target.value) })}
            placeholder="10-digit mobile"
            hint={editUser?.role === "manager" ? "New-booking alerts go to this number on WhatsApp. Use the manager's own number, not the business WhatsApp number." : undefined}
          />
          {editUser && editUser.role !== "customer" && (
            <>
              <Select label="Role" value={editForm.role} onChange={(e) => setEditForm({ ...editForm, role: e.target.value as UserRole })}>
                <option value="captain">Captain</option>
                <option value="manager">Manager</option>
                <option value="admin">Admin</option>
              </Select>
              <Select
                label="Service Center"
                value={editForm.service_center_id}
                onChange={(e) => setEditForm({ ...editForm, service_center_id: e.target.value })}
              >
                <option value="">Not Assigned</option>
                {(centers?.data || []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
              {centersError}
              {editForm.role === "manager" && !editForm.service_center_id && (
                <p className="text-xs text-amber-600">
                  A manager with no center can't sell/assign plans, or see their own Subscriptions or KPI pages.
                </p>
              )}
            </>
          )}
          {editError && <p className="text-sm text-[var(--color-error)]">{editError}</p>}
          <Button type="submit" className="w-full" isLoading={updateMutation.isPending}>
            Save Changes
          </Button>
        </form>
      </Modal>

      <CustomerDetailDrawer customerId={detailCustomerId} onClose={() => setDetailCustomerId(null)} />
      <StaffResetPasswordDialog staff={resetFor} onClose={() => setResetFor(null)} />
    </div>
  );
}
