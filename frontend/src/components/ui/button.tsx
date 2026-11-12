import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

const buttonStyles = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl font-medium transition-all duration-150 disabled:pointer-events-none disabled:opacity-45 active:scale-[0.985] [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-brand text-[#04202a] shadow-[0_6px_20px_-8px_rgb(34_211_238/0.75)] hover:brightness-110",
        secondary: "bg-surface-3 text-fg hover:bg-border",
        outline: "border border-border bg-transparent text-fg hover:border-brand/60 hover:bg-surface-2",
        ghost: "bg-transparent text-muted hover:bg-surface-2 hover:text-fg",
        danger: "bg-danger text-white shadow-[0_6px_20px_-8px_rgb(244_63_94/0.7)] hover:brightness-110",
        success: "bg-ok text-[#052e16] hover:brightness-110",
        warn: "bg-warn text-[#301a00] hover:brightness-110",
        link: "bg-transparent text-brand underline-offset-4 hover:underline",
      },
      size: {
        xs: "h-7 px-2 text-[11px] [&_svg]:size-3.5",
        sm: "h-8.5 px-3 text-xs [&_svg]:size-4",
        md: "h-10 px-4 text-sm [&_svg]:size-4.5",
        lg: "h-12 px-6 text-base [&_svg]:size-5",
        icon: "size-9.5 [&_svg]:size-4.5",
        "icon-sm": "size-8 [&_svg]:size-4",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonStyles> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, loading, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(buttonStyles({ variant, size }), className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? (
        <span
          aria-hidden
          className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      ) : null}
      {children}
    </button>
  );
});

export { buttonStyles };
