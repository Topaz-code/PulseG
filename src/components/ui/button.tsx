import { forwardRef } from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * The button, in the four weights the app uses.
 *
 * `primary` is the canary-yellow call to action. Each screen marks exactly one of these with
 * `data-primary-action` - the design audit counts them per view, because "two yellow buttons" is
 * how a screen stops telling the user what to do next.
 *
 * Every variant carries hover, active, disabled and focus states. A button that only looks right
 * at rest is not finished.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-app-background disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-foreground hover:bg-canary-300 active:bg-canary-400",
        secondary:
          "bg-surface-700 text-mint-100 hover:bg-surface-600 active:bg-surface-500 border border-app-border",
        ghost: "bg-transparent text-mint-200 hover:bg-surface-800 active:bg-surface-700",
        danger:
          "bg-danger-solid text-danger-foreground hover:bg-danger-400 active:bg-danger-600 focus-visible:ring-danger-soft",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-10 px-4 text-sm",
        lg: "h-12 px-6 text-base",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
    loading?: boolean;
  };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, asChild = false, loading = false, children, disabled, ...rest },
  ref,
) {
  const Component = asChild ? Slot : "button";
  return (
    <Component
      ref={ref}
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {children}
    </Component>
  );
});

export { buttonVariants };
