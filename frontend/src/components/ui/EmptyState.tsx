import type { LucideIcon } from "lucide-react";
import { PackageOpen } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
  icon: Icon = PackageOpen,
  title,
  description,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-gray-200 px-6 py-16 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-[var(--color-primary-light)]">
        <Icon className="h-6 w-6 text-[var(--color-primary)]" />
      </div>
      <h3 className="text-base font-semibold text-[var(--color-text-primary)]">{title}</h3>
      {description && <p className="mt-1.5 max-w-sm text-sm text-[var(--color-text-secondary)]">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
