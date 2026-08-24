import { type ButtonHTMLAttributes, forwardRef } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "../../lib/cn";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger" | "success" | "info";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
}

const variantClasses: Record<string, string> = {
  primary: "bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-dark)] shadow-[var(--shadow-soft)]",
  secondary: "bg-[var(--color-accent)] text-white hover:brightness-95",
  outline: "border border-gray-300 text-[var(--color-text-primary)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] bg-white",
  ghost: "text-[var(--color-text-primary)] hover:bg-gray-100",
  danger: "bg-[var(--color-error)] text-white hover:brightness-95",
  success: "bg-[var(--color-success)] text-white hover:brightness-95",
  info: "bg-blue-600 text-white hover:bg-blue-700",
};

const sizeClasses: Record<string, string> = {
  sm: "text-sm px-3 py-1.5 gap-1.5",
  md: "text-sm px-4 py-2.5 gap-2",
  lg: "text-base px-6 py-3 gap-2",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", isLoading, disabled, children, ...props }, ref) => {
    return (
      <button
        ref={ref}
        disabled={disabled || isLoading}
        className={cn(
          "inline-flex items-center justify-center rounded-full font-medium transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed",
          variantClasses[variant],
          sizeClasses[size],
          className
        )}
        {...props}
      >
        {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
        {children}
      </button>
    );
  }
);
Button.displayName = "Button";
