import { Bike, CarFront } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { vehicleTypeApi } from "../../api/catalog";
import { BIKE_WORD } from "../../lib/serviceMix";

/**
 * A car or a bike, whichever this vehicle actually is.
 *
 * Vehicle types are admin-managed rows, not an enum, so the only honest
 * signal is the type's NAME — the same `BIKE_WORD` test the booking rules
 * already use to decide which services a vehicle can have (lib/serviceMix),
 * so the icon can never disagree with what the catalogue offers.
 *
 * Falls back to the car icon while the type list is loading or when a type
 * has been deleted: showing the wrong icon for a moment is better than a
 * hole in the layout.
 */
export function VehicleIcon({
  vehicleTypeId,
  className = "h-4 w-4",
}: {
  vehicleTypeId?: string | null;
  className?: string;
}) {
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const name = vehicleTypes?.find((t) => t.id === vehicleTypeId)?.name || "";
  const Icon = BIKE_WORD.test(name) ? Bike : CarFront;
  return <Icon className={className} aria-hidden="true" />;
}

/** Same decision without the component, for places already holding the list. */
export function isBikeType(typeName?: string | null): boolean {
  return BIKE_WORD.test(typeName || "");
}
