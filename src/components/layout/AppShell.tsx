import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useActiveProject, useFirstRun } from "@/lib/queries";
import { useEventStream } from "@/lib/ws";
import { useStudio } from "@/lib/store";
import { TopBar } from "./TopBar";
import { Sidebar } from "./Sidebar";
import { ContextRail } from "./ContextRail";
import { CommandBar } from "./CommandBar";
import { ProjectGate } from "./ProjectGate";
import { TaskDrawer } from "@/components/TaskDrawer";
import { PreviewPanel } from "@/components/PreviewPanel";
import { SetupWizard } from "@/components/SetupWizard";
import { ErrorNote, Toaster } from "@/components/ui/feedback";
import { IconChevronRight } from "@/components/Icons";

/**
 * The window: top bar, left rail, main panel, planning rail with the preview under it, and the
 * command bar along the bottom.
 *
 * It also owns the two things that must exist exactly once per window: the live event stream and
 * the first-run wizard. Both are here rather than in a view so navigating between screens cannot
 * open a second socket or show the wizard twice.
 */
export function AppShell() {
  const { connection } = useEventStream();
  const { data: firstRun } = useFirstRun();
  const { data: active, error } = useActiveProject();
  const railOpen = useStudio((state) => state.railOpen);
  const toggleRail = useStudio((state) => state.toggleRail);
  const toast = useStudio((state) => state.toast);
  const clearToast = useStudio((state) => state.clearToast);
  const showToast = useStudio((state) => state.showToast);
  // One instance per window: the wizard opens when the studio has never been set up, and the
  // user can reopen it from the project screen without a second copy existing.
  const [wizardOpen, setWizardOpen] = useState(false);

  useEffect(() => {
    if (firstRun && !firstRun.first_run_complete) setWizardOpen(true);
  }, [firstRun, setWizardOpen]);

  useEffect(() => {
    if (connection === "closed") {
      showToast({
        title: "Reconnecting to the studio",
        body: "Live updates paused. Your work is safe - the studio reconnects on its own.",
        tone: "info",
      });
    }
  }, [connection, showToast]);

  const needsProject = error instanceof ApiError && error.needsProject;

  return (
    <div className="flex h-full flex-col bg-app-background">
      <TopBar />

      <div className="flex min-h-0 flex-1">
        <Sidebar />

        <main className="min-w-0 flex-1 overflow-hidden">
          {active?.project || !needsProject ? (
            <Outlet />
          ) : (
            <ProjectGate />
          )}
        </main>

        <aside
          className={cn(
            "relative flex shrink-0 flex-col border-l border-app-border bg-app-surface transition-transform duration-base",
            railOpen ? "w-[360px]" : "w-10",
          )}
          aria-label="Planning and preview"
        >
          <button
            type="button"
            onClick={toggleRail}
            aria-expanded={railOpen}
            aria-label={railOpen ? "Hide the planning panel" : "Show the planning panel"}
            className={cn(
              "absolute -left-3 top-4 z-20 flex h-6 w-6 items-center justify-center rounded-full border border-app-border bg-charcoal-800 text-mint-200",
              "transition-colors duration-fast hover:bg-surface-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
            )}
          >
            <IconChevronRight className={cn("transition-transform duration-base", railOpen && "rotate-180")} size={14} />
          </button>

          {railOpen ? (
            <>
              <div className="min-h-0 flex-1">
                <ContextRail />
              </div>
              <PreviewPanel />
            </>
          ) : null}
        </aside>
      </div>

      {error && !needsProject ? (
        <div className="px-4 pb-2">
          <ErrorNote>{error.message}</ErrorNote>
        </div>
      ) : null}

      <CommandBar />
      <TaskDrawer />
      <Toaster toast={toast} onDismiss={clearToast} />
      <SetupWizard open={wizardOpen} onFinished={() => setWizardOpen(false)} />
    </div>
  );
}
