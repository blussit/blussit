import type { ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost";
}

const variants = {
  primary:
    "bg-gold text-ink hover:bg-gold/90 shadow-[0_8px_24px_-8px_rgba(245,181,27,0.55)]",
  secondary:
    "bg-transparent text-ink border border-ink/15 hover:border-ink/30",
  ghost: "bg-white/10 text-white border border-white/20 hover:bg-white/15",
};

export function Button({
  variant = "primary",
  className = "",
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-full px-6 py-3.5 text-[15px] font-semibold transition-all duration-200 active:scale-[0.98] disabled:opacity-60 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
