import { useVehicleTypeNames } from "../../lib/vehicleTypeNames";

/** " · Removed" after a vehicle-type name whose type was deleted. Staff screens only. */
export default function RemovedTypeTag({ typeId }: { typeId?: string | null }) {
  const { isRemoved, ready } = useVehicleTypeNames();
  if (!ready || !typeId || !isRemoved(typeId)) return null;
  return <span className="ml-1 rounded bg-gray-100 px-1.5 py-0.5 align-middle text-[10px] font-medium uppercase tracking-wide text-gray-500">Removed</span>;
}
