import { Loader2 } from "lucide-react";
import { cn } from "../../lib/cn";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-5 w-5 animate-spin text-[var(--color-primary)]", className)} />;
}

export function PageLoader() {
  return (
    <div className="flex h-64 w-full items-center justify-center">
      <Spinner className="h-8 w-8" />
    </div>
  );
}
