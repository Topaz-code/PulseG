import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Overview } from "@/views/Overview";
import { TaskBoard } from "@/views/TaskBoard";
import { DesignView } from "@/views/DesignView";
import { Assets } from "@/views/Assets";
import { Knowledge } from "@/views/Knowledge";
import { AgentDetail } from "@/views/AgentDetail";
import { Logs } from "@/views/Logs";
import { GitHistory } from "@/views/GitHistory";
import { Settings } from "@/views/Settings";

/**
 * Nine screens, one shell.
 *
 * The shell owns the top bar, the two rails, the command bar, the task drawer, the live event
 * stream and the Setup Wizard, so no view has to think about any of that. A route added here gets
 * the whole studio around it for free.
 */
export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Overview />} />
        <Route path="board" element={<TaskBoard />} />
        <Route path="design" element={<DesignView />} />
        <Route path="assets" element={<Assets />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="agents/:agentId" element={<AgentDetail />} />
        <Route path="logs" element={<Logs />} />
        <Route path="git" element={<GitHistory />} />
        <Route path="settings" element={<Settings />} />
        {/* An old bookmark or a stale link should land somewhere real rather than on a blank
            screen, which is what a 404 inside a desktop app looks like to a non-technical user. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
