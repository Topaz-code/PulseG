import { cn } from "@/lib/utils";
import { Tooltip } from "./ui/feedback";

const PHASES = [
  "Design",
  "Playable Core",
  "Content",
  "Polish",
  "Audio and Art Pass",
  "Ship",
];

/**
 * Where the game is in its build, in six dots.
 *
 * Deliberately not a progress bar: phases are gates, and a bar would suggest the work is a
 * single continuous thing. A dot is filled when its phase is approved, ringed when it is the
 * current one.
 */
export function PhaseDots({
  current,
  approved = 0,
  className,
}: {
  current: number;
  approved?: number;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-2", className)} role="group" aria-label="Build phases">
      {PHASES.map((name, index) => {
        const number = index + 1;
        const done = number <= approved;
        const active = number === current;
        return (
          <Tooltip key={name} label={`Phase ${number}: ${name}${done ? " (approved)" : active ? " (in progress)" : ""}`}>
            <span
              tabIndex={0}
              className={cn(
                "h-2.5 w-2.5 rounded-full transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                done ? "bg-teal-400" : active ? "bg-accent ring-2 ring-accent/40" : "bg-surface-600",
              )}
            />
          </Tooltip>
        );
      })}
      <span className="text-xs text-muted ml-1">
        Phase {current}: {PHASES[Math.max(0, Math.min(PHASES.length - 1, current - 1))]}
      </span>
    </div>
  );
}
