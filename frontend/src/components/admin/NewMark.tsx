import { Check } from "lucide-react";
import { Badge, Button } from "../ui";

/** A small "New" pill for rows an admin has not opened yet. */
export function NewMark() {
  return (
    <Badge tone="error" className="ml-2 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide">
      New
    </Badge>
  );
}

/** "Mark All As Read" for an inbox page; disabled while nothing is new. */
export function MarkAllReadButton({ onClick, disabled, busy }: { onClick: () => void; disabled: boolean; busy?: boolean }) {
  return (
    <Button variant="outline" size="sm" onClick={onClick} disabled={disabled} isLoading={busy}>
      <Check className="h-4 w-4" /> Mark All As Read
    </Button>
  );
}
