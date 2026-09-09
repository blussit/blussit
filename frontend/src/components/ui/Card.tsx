import { type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      // Border color is themeable: brand portals (customer/captain shells)
      // set --color-card-border to a light yellow so boxes read on their
      // white ground; everywhere else it falls back to gray-100.
      className={cn("rounded-[var(--radius-card)] border border-[var(--color-card-border,#f3f4f6)] bg-white shadow-[var(--shadow-soft)]", className)}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("border-b border-gray-100 px-6 py-4", className)} {...props} />;
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-6", className)} {...props} />;
}
