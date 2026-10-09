import type { ComponentProps, ReactNode } from "react";
import { Link, type LinkProps } from "react-router";

import { cn } from "../../lib/cn";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

const variantClasses: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-ink hover:bg-accent-hover shadow-sm font-semibold",
  secondary: "bg-surface-2 text-ink border border-line hover:border-line-strong hover:bg-surface-2/70",
  ghost: "text-ink-muted hover:text-ink hover:bg-surface-2",
  danger: "bg-fail/10 text-fail border border-fail/30 hover:bg-fail/20",
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-[13px] gap-1.5 [&_svg]:size-3.5",
  md: "h-9 px-4 text-sm gap-2 [&_svg]:size-4",
};

export function buttonClasses(variant: ButtonVariant, size: ButtonSize, className?: string): string {
  return cn(
    "inline-flex select-none items-center justify-center whitespace-nowrap rounded-md font-medium transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
    "disabled:cursor-not-allowed disabled:opacity-50",
    variantClasses[variant],
    sizeClasses[size],
    className,
  );
}

export interface ButtonProps extends ComponentProps<"button"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  /** leading icon, usually a lucide icon element */
  icon?: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  icon,
  className,
  children,
  disabled,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button type={type} disabled={disabled || loading} className={buttonClasses(variant, size, className)} {...props}>
      {loading ? <Spinner className="size-4" /> : icon}
      {children}
    </button>
  );
}

export interface LinkButtonProps extends LinkProps {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
}

/** A router link that looks like a button. */
export function LinkButton({ variant = "secondary", size = "md", icon, className, children, ...props }: LinkButtonProps) {
  return (
    <Link className={buttonClasses(variant, size, className)} {...props}>
      {icon}
      {children}
    </Link>
  );
}
