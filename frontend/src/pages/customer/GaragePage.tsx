/**
 * My Garage — every car the customer has: saved vehicles merged with the
 * cars from their booking history (GET /vehicles/garage, one row per car:
 * vehicle record, else plate, else vehicle type). Saved cars are edited /
 * removed here (POST/PUT/DELETE /vehicles); a booked-only car can get its
 * number saved. Each car books in one tap: its newest finished wash is
 * replayed (same services and address), or a fresh booking for its type.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Bike, CarFront, Pencil, Plus, Trash2 } from "lucide-react";
import { vehicleTypeApi } from "../../api/catalog";
import { vehicleApi, type GarageCar } from "../../api/profile";
import { Input, Modal } from "../../components/ui";
import { btn, card, PageHeader, Skeleton } from "../../components/customer/ui";
import { GARAGE_QUERY_KEY, garageCta, garagePlate, garageRebookPath, garageTitle, garageTypeLine, garageWashLines, useGarageServices } from "../../components/customer/cars";
import { titleCase } from "../../components/public/landing/shared";
import { isBikeType } from "../../components/shared/VehicleIcon";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { cn } from "../../lib/cn";
import { PLATE_FORMAT_HINT, validateIndianPlate } from "../../lib/validators";

const EMPTY = { vehicle_type: "", brand: "", model: "", registration_number: "", is_default: false };

export default function GaragePage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [params, setParams] = useSearchParams();
  const { data: cars, isLoading, isError, refetch } = useQuery({ queryKey: GARAGE_QUERY_KEY, queryFn: vehicleApi.garage, staleTime: 30_000 });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const saved = (cars || []).filter((c) => c.saved);
  const carServices = useGarageServices(cars);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<GarageCar | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");

  /** `vehicleType`: a booked-only car getting its number saved. */
  const openAdd = (vehicleType?: string | null) => {
    setEditing(null);
    setForm({ ...EMPTY, vehicle_type: vehicleType || vehicleTypes?.[0]?.id || "", is_default: !saved.length });
    setError("");
    setOpen(true);
  };
  const openEdit = (c: GarageCar) => {
    setEditing(c);
    setForm({ vehicle_type: c.vehicle_type || "", brand: c.brand || "", model: c.model || "", registration_number: c.registration_number || "", is_default: c.is_default });
    setError("");
    setOpen(true);
  };
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["vehicles"] });
    queryClient.invalidateQueries({ queryKey: GARAGE_QUERY_KEY });
  };

  // /app/garage?add=1 (Home's "Add your car") opens the form straight away.
  useEffect(() => {
    if (params.get("add") !== "1" || !vehicleTypes) return;
    openAdd();
    const next = new URLSearchParams(params);
    next.delete("add");
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, vehicleTypes]);

  const save = useMutation({
    mutationFn: async () => {
      const payload = {
        vehicle_type: form.vehicle_type,
        brand: form.brand.trim(),
        model: form.model.trim(),
        registration_number: form.registration_number.replace(/\s+/g, "").toUpperCase(),
        is_default: form.is_default,
      };
      // The server's rule, checked here so a typo reads as a clear message
      // (a 422 would only say "Request validation failed").
      if (!validateIndianPlate(payload.registration_number)) throw new Error("BAD_PLATE");
      const plateChanged = !editing || editing.registration_number !== payload.registration_number;
      const plateKey = (v: string) => v.replace(/[^A-Z0-9]/gi, "").toUpperCase();
      if (plateChanged && saved.some((c) => c.vehicle_id !== editing?.vehicle_id && plateKey(c.registration_number || "") === plateKey(payload.registration_number))) {
        throw new Error("ALREADY_IN_GARAGE");
      }
      let acknowledge = false;
      // Same plate on another account: ask once, the server re-checks anyway.
      if (plateChanged) {
        const check = await vehicleApi.checkRegistration(payload.registration_number);
        if (check.already_registered) {
          const ok = await confirm({
            title: "This Number Is On Another Account",
            message: "If it's your car (e.g. a family member booked it before), add it anyway.",
            confirmLabel: "Add Anyway",
            tone: "default",
          });
          if (!ok) return null;
          acknowledge = true;
        }
      }
      const body = acknowledge ? { ...payload, acknowledge_shared_registration: true } : payload;
      return editing?.vehicle_id ? vehicleApi.update(editing.vehicle_id, body) : vehicleApi.create(body);
    },
    onSuccess: (result) => {
      if (!result) return;
      refresh();
      setOpen(false);
    },
    onError: (err) =>
      setError(
        err instanceof Error && err.message === "ALREADY_IN_GARAGE"
          ? "This vehicle is already in your garage."
          : err instanceof Error && err.message === "BAD_PLATE"
            ? `Enter a valid registration number, ${PLATE_FORMAT_HINT}.`
            : getErrorMessage(err),
      ),
  });

  const remove = useMutation({
    mutationFn: (id: string) => vehicleApi.remove(id),
    onSuccess: () => {
      setListError("");
      refresh();
    },
    onError: (err) => setListError(getErrorMessage(err)),
  });

  const valid = !!form.vehicle_type && form.brand.trim().length > 0 && form.model.trim().length > 0 && form.registration_number.replace(/\s+/g, "").length >= 6;
  const list = cars || [];

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <PageHeader
        back="/app/profile"
        title="My Garage"
        right={
          list.length > 0 && (
            <button type="button" onClick={() => openAdd()} className={btn("soft", "sm", "hidden sm:inline-flex")}>
              <Plus className="h-4 w-4" /> Add Vehicle
            </button>
          )
        }
      />

      {listError && <p className="text-sm text-[#C62828]">{listError}</p>}

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Skeleton className="h-[188px]" />
          <Skeleton className="h-[188px]" />
        </div>
      ) : isError ? (
        <div className={`${card} p-6 text-center`}>
          <p className="text-sm text-[#5F6878]">Couldn't load your vehicles.</p>
          <button type="button" className={btn("outline", "sm", "mt-3")} onClick={() => void refetch()}>
            Try Again
          </button>
        </div>
      ) : (
        <>
          {list.length === 0 && (
            <div className="rounded-2xl border border-dashed border-[#CFDCF0] bg-[#F7FAFF] px-6 py-8 text-center">
              <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-white text-[#0A66F0] shadow-sm">
                <CarFront className="h-7 w-7" />
              </span>
              <p className="mt-3 font-display text-base font-bold text-[#0E1A33]">Your Garage Is Empty</p>
              <p className="mt-1 text-sm text-[#5F6878]">Every car you get washed shows up here. Add one now to book it in one tap.</p>
            </div>
          )}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {list.map((c) => {
              const title = garageTitle(c);
              const typeLine = garageTypeLine(c);
              const plate = garagePlate(c);
              const washLines = garageWashLines(c, carServices);
              const Icon = isBikeType(c.vehicle_type_name) ? Bike : CarFront;
              return (
                <div key={c.id} className={`${card} flex min-w-0 flex-col p-4`}>
                  <div className="flex items-start gap-4">
                    <span className="flex h-[72px] w-[84px] shrink-0 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,#EEF3FA,#E8F0FE)] text-[#0E1A33] sm:w-[92px]">
                      <Icon className="h-10 w-10" strokeWidth={1.6} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-2">
                        <p className={cn("truncate text-[17px] font-semibold text-[#0E1A33]", title === c.registration_number && "tabular-nums tracking-wide")}>{title}</p>
                        {c.saved && c.vehicle_id && (
                          <div className="-mr-1.5 -mt-1 flex shrink-0">
                            <button type="button" aria-label="Edit vehicle" onClick={() => openEdit(c)} className="rounded-full p-2 text-[#8A94A6] transition-colors hover:bg-[#EEF3FA] hover:text-[#0E1A33]">
                              <Pencil className="h-4 w-4" />
                            </button>
                            <button
                              type="button"
                              aria-label="Remove vehicle"
                              onClick={async () => {
                                if (await confirm({ title: `Remove ${title}?`, message: "Your past bookings stay as they are.", confirmLabel: "Remove", tone: "danger" })) remove.mutate(c.vehicle_id!);
                              }}
                              className="rounded-full p-2 text-[#8A94A6] transition-colors hover:bg-[#FDECEC] hover:text-[#C62828]"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </div>
                        )}
                      </div>
                      {(typeLine || c.is_default) && (
                        <p className="text-sm text-[#5F6878]">
                          {typeLine}
                          {c.is_default && <span className={cn("rounded-full bg-[#E8F0FE] px-2 py-0.5 text-[11px] font-semibold text-[#0A66F0]", typeLine && "ml-2")}>Default</span>}
                        </p>
                      )}
                      {plate && <p className="mt-1 tabular-nums text-sm tracking-wide text-[#0E1A33]">{plate}</p>}
                      {washLines.length ? (
                        washLines.map((line) => (
                          <p key={line} className="mt-1 truncate text-xs text-[#5F6878]">
                            {line}
                          </p>
                        ))
                      ) : (
                        <p className="mt-1 truncate text-xs text-[#5F6878]">{c.saved ? "Not Washed Yet" : ""}</p>
                      )}
                    </div>
                  </div>
                  <div className="mt-auto flex gap-2 pt-4">
                    {!c.saved && !c.registration_number && (
                      <button type="button" onClick={() => openAdd(c.vehicle_type)} className={btn("outline", "md", "px-3.5")}>
                        <Plus className="h-4 w-4" /> Add Number
                      </button>
                    )}
                    <Link to={garageRebookPath(c)} className={btn("primary", "md", "min-w-0 flex-1")}>
                      {garageCta(c)}
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
          <button
            type="button"
            onClick={() => openAdd()}
            className="flex h-14 w-full items-center justify-center gap-2 rounded-2xl border border-[#CFDCF0] bg-[#F5F9FF] text-[15px] font-semibold text-[#0A66F0] transition-colors hover:border-[#0A66F0]"
          >
            <Plus className="h-5 w-5" /> Add New Vehicle
          </button>
        </>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title={editing ? "Edit Vehicle" : "Add A Vehicle"}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            if (valid) save.mutate();
          }}
        >
          <div>
            <p className="mb-2 text-sm font-medium text-[#0E1A33]">Type</p>
            <div className="flex flex-wrap gap-2">
              {(vehicleTypes || []).map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, vehicle_type: t.id }))}
                  className={cn(
                    "rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors",
                    form.vehicle_type === t.id ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] text-[#5F6878] hover:border-[#CFDCF0]"
                  )}
                >
                  {titleCase(t.name)}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Brand" placeholder="e.g. Honda" value={form.brand} onChange={(e) => setForm((f) => ({ ...f, brand: e.target.value }))} />
            <Input label="Model" placeholder="e.g. City" value={form.model} onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))} />
          </div>
          <Input
            label="Registration Number"
            placeholder="MP09AB1234"
            autoCapitalize="characters"
            value={form.registration_number}
            onChange={(e) => setForm((f) => ({ ...f, registration_number: e.target.value.toUpperCase() }))}
          />
          <label className="flex items-center gap-2 text-sm text-[#5F6878]">
            <input type="checkbox" checked={form.is_default} onChange={(e) => setForm((f) => ({ ...f, is_default: e.target.checked }))} />
            My Main Vehicle
          </label>
          {error && <p className="text-sm text-[#C62828]">{error}</p>}
          <button type="submit" className={btn("primary", "md", "w-full")} disabled={!valid || save.isPending}>
            {save.isPending ? "Saving…" : editing ? "Save Changes" : "Add Vehicle"}
          </button>
        </form>
      </Modal>
    </div>
  );
}
