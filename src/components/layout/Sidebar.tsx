import { NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";
import { useActiveProject, useNotifications, useReviewQueue } from "@/lib/queries";
import {
  IconBoard,
  IconBook,
  IconBranch,
  IconDashboard,
  IconImages,
  IconRobot,
  IconSearch,
  IconSettings,
  IconTerminal,
} from "@/components/Icons";

/**
 * The left rail. Nine destinations, in the order the work happens: see the whole studio, work
 * the queue, read the design, look at the art, search what the team has learned, watch the log,
 * check the history, tune the settings.
 *
 * Counts are live badges rather than decoration: the review count is the number that tells a
 * person whether they are needed right now.
 */
const NAV = [
  { to: "/", label: "Overview", icon: IconDashboard, end: true },
  { to: "/board", label: "Task Board", icon: IconBoard, badge: "review" as const },
  { to: "/design", label: "GDD and Story", icon: IconBook },
  { to: "/assets", label: "Assets", icon: IconImages },
  { to: "/knowledge", label: "Knowledge", icon: IconSearch },
  { to: "/agents", label: "Agents", icon: IconRobot },
  { to: "/logs", label: "Logs and Terminal", icon: IconTerminal, badge: "unread" as const },
  { to: "/git", label: "Git History", icon: IconBranch },
  { to: "/settings", label: "Settings", icon: IconSettings },
];

export function Sidebar() {
  const { data: review } = useReviewQueue();
  const { data: notifications } = useNotifications();
  const { data: active } = useActiveProject();
  const paused = active?.overview?.stats?.tasks_paused ?? 0;

  const badges: Record<string, number> = {
    review: (review?.count ?? 0) + paused,
    unread: notifications?.unread ?? 0,
  };

  return (
    <nav
      aria-label="Main"
      className="flex w-60 shrink-0 flex-col gap-1 border-r border-app-border bg-app-surface px-2 py-3"
    >
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors duration-fast",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
              isActive
                ? "bg-charcoal-800 text-canary-200"
                : "text-mint-200 hover:bg-surface-700 hover:text-mint-100",
            )
          }
        >
          {({ isActive }) => (
            <>
              <item.icon size={18} className={isActive ? "text-canary-200" : "text-muted"} />
              <span className="truncate">{item.label}</span>
              {item.badge && badges[item.badge] > 0 ? (
                <span className="ml-auto flex h-5 min-w-5 items-center justify-center rounded-full bg-accent px-1.5 text-[11px] font-semibold text-accent-foreground">
                  {badges[item.badge]}
                </span>
              ) : null}
            </>
          )}
        </NavLink>
      ))}

      <div className="mt-auto px-3 pb-2 text-xs text-muted">
        {active?.project ? (
          <p className="truncate" title={active.project.path}>
            {active.project.path}
          </p>
        ) : (
          <p>No project open yet.</p>
        )}
      </div>
    </nav>
  );
}
