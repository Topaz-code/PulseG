import { forwardRef, type ReactNode } from "react";
import * as LabelPrimitive from "@radix-ui/react-label";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

export const Label = forwardRef<HTMLLabelElement, LabelPrimitive.LabelProps & { hint?: string }>(
  function Label({ className, children, hint, ...rest }, ref) {
    return (
      <LabelPrimitive.Root
        ref={ref}
        className={cn("block text-xs font-medium text-mint-200 mb-2", className)}
        {...rest}
      >
        {children}
        {hint ? <span className="block text-muted font-normal mt-1">{hint}</span> : null}
      </LabelPrimitive.Root>
    );
  },
);

const control =
  "w-full rounded-md border border-app-border bg-charcoal-800 px-3 py-2 text-sm text-mint-100 placeholder:text-muted transition-colors duration-fast hover:border-surface-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-app-background disabled:opacity-50 disabled:cursor-not-allowed";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cn(control, "h-10", className)} {...rest} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cn(control, "min-h-[80px] resize-y", className)} {...rest} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <select ref={ref} className={cn(control, "h-10 pr-8", className)} {...rest}>
        {children}
      </select>
    );
  },
);

export function Switch({
  checked,
  onCheckedChange,
  label,
  detail,
  disabled,
}: {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  label: ReactNode;
  detail?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="min-w-0">
        <div className="text-sm text-mint-100">{label}</div>
        {detail ? <div className="text-xs text-muted mt-1">{detail}</div> : null}
      </div>
      <SwitchPrimitive.Root
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className={cn(
          "relative h-6 w-11 shrink-0 rounded-full transition-colors duration-fast",
          "bg-surface-700 hover:bg-surface-600 data-[state=checked]:bg-accent",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-app-background",
          "disabled:opacity-50 disabled:cursor-not-allowed",
        )}
      >
        <SwitchPrimitive.Thumb className="block h-5 w-5 translate-x-0.5 rounded-full bg-mint-100 transition-transform duration-fast data-[state=checked]:translate-x-[22px]" />
      </SwitchPrimitive.Root>
    </div>
  );
}

export function FieldRow({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="mb-4">
      <Label hint={hint}>{label}</Label>
      {children}
    </div>
  );
}
