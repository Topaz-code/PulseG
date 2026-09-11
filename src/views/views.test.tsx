/**
 * The nine views, rendered against a fake backend built from real response shapes.
 *
 * This is the check that catches what TypeScript cannot: a view that compiles cleanly, then
 * renders nothing, because it reads a field the API does not send. The fixture layer in
 * `src/test/fixtures.ts` throws on an unmapped endpoint for the same reason - a screen that
 * starts calling a new URL fails here instead of quietly showing an empty state.
 *
 * It is deliberately not a snapshot test. Snapshots pass on a screen made entirely of empty boxes;
 * these assertions name the thing a person came to the screen to read.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { installFetch } from "@/test/fixtures";
import { AgentDetail } from "@/views/AgentDetail";
import { Assets } from "@/views/Assets";
import { DesignView } from "@/views/DesignView";
import { GitHistory } from "@/views/GitHistory";
import { Knowledge } from "@/views/Knowledge";
import { Logs } from "@/views/Logs";
import { Overview } from "@/views/Overview";
import { Settings } from "@/views/Settings";
import { TaskBoard } from "@/views/TaskBoard";

let restore: () => void;

beforeEach(() => {
  restore = installFetch();
});

afterEach(() => {
  restore();
});

/**
 * Render one view the way the shell does.
 *
 * `path` is the route pattern, not the URL: Agent Detail reads its agent from `useParams`, so it
 * needs a real `<Route>` above it. Everything else is rendered at "/".
 */
function renderView(ui: React.ReactElement, url = "/", pattern = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path={pattern} element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Nothing on any screen should ever show these to a person. */
function expectNoLeakedInternals(container: HTMLElement) {
  const text = container.textContent ?? "";
  expect(text).not.toContain("undefined");
  expect(text).not.toContain("NaN");
  expect(text).not.toContain("[object Object]");
  expect(text).not.toMatch(/\bnull\b/);
}

describe("Overview", () => {
  it("shows the project, the phase and the work waiting for a decision", async () => {
    const { container } = renderView(<Overview />);
    expect(await screen.findByText("Harbour Lights")).toBeTruthy();
    // The one thing this screen exists to say: something needs a human.
    expect((await screen.findAllByText(/waiting for your review/i)).length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/2 of 6 tasks approved/);
    expect(container.textContent).toMatch(/Mistral/);
    expectNoLeakedInternals(container);
  });
});

describe("TaskBoard", () => {
  it("renders all seven lanes with their tasks", async () => {
    const { container } = renderView(<TaskBoard />);
    // The lane headings are the board's contract with the task lifecycle, in order.
    for (const lane of [
      "Waiting",
      "In Progress",
      "In Audit",
      "Needs Your Review",
      "Approved",
      "Declined, Retrying",
      "Needs Intervention",
    ]) {
      await waitFor(() => expect(screen.getAllByText(lane).length).toBeGreaterThan(0));
    }
    // The task cards themselves, not just the lane headings.
    expect((await screen.findAllByText("Implement the lamp beam")).length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/TASK_004/);
    expectNoLeakedInternals(container);
  });
});

describe("DesignView", () => {
  it("renders the design document as Markdown", async () => {
    const { container } = renderView(<DesignView />);
    expect(await screen.findByText("Game Design and Story")).toBeTruthy();
    // The document list, then the body of the selected document.
    expect((await screen.findAllByText("Gdd")).length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/A cosy platformer about a lighthouse keeper/);
    expect(container.textContent).toMatch(/MECHANICS/);
    expectNoLeakedInternals(container);
  });
});

describe("Assets", () => {
  it("lists the library, the style lock and the request state", async () => {
    const { container } = renderView(<Assets />);
    // Two files in the fixture: a sprite and a track the user added.
    expect(await screen.findByText(/2 files,/)).toBeTruthy();
    expect(await screen.findByText("lighthouse_keeper.png")).toBeTruthy();
    expect(container.textContent).toMatch(/Warm 32x32 pixel art, dusk palette/);
    // The API flags what a browser can play, and the screen keeps sound in its own section.
    expect(container.textContent).toMatch(/Sound \(1\)/);
    expect(container.textContent).toMatch(/waves\.ogg/);
    expectNoLeakedInternals(container);
  });
});

describe("AgentDetail", () => {
  it("shows the model chain that will be written back to agents.yaml", async () => {
    const { container } = renderView(<AgentDetail />, "/agents/programmer", "/agents/:agentId");
    // Wait for the roster row rather than the name in the breadcrumb, which renders first.
    await waitFor(() => expect(container.textContent).toMatch(/Writes the Godot scenes and GDScript/));
    await waitFor(() => expect(container.textContent).toMatch(/mistral-large-latest/));
    expectNoLeakedInternals(container);
  });
});

describe("Logs", () => {
  it("shows the live tail with the agent that produced each line", async () => {
    const { container } = renderView(<Logs />);
    expect(await screen.findByText("Logs and Live Terminal")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("wrote godot_project/scripts/lamp.gd")).toBeTruthy());
    // The colour-coding is driven by the agent id on the event payload, so the name must be there.
    expect(screen.getAllByText("Programmer").length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/TASK_004/);
    expectNoLeakedInternals(container);
  });
});

describe("GitHistory", () => {
  it("lists the commits the human approvals produced", async () => {
    const { container } = renderView(<GitHistory />);
    expect(await screen.findByText(/1 saved point/)).toBeTruthy();
    expect(await screen.findByText("Freeze the design summary")).toBeTruthy();
    expect(container.textContent).toMatch(/phase\/1-playable-core/);
    expectNoLeakedInternals(container);
  });
});

describe("Knowledge", () => {
  it("renders the empty state honestly when nothing is indexed", async () => {
    const { container } = renderView(<Knowledge />);
    expect(await screen.findByText("Knowledge Base")).toBeTruthy();
    // An empty knowledge base says so, in words, rather than showing a blank pane.
    expect(await screen.findByText(/Nothing collected yet/)).toBeTruthy();
    expect(container.textContent).toMatch(/0 entries from 0 sources/);
    expectNoLeakedInternals(container);
  });
});

describe("Settings", () => {
  it("shows keys, Godot, git and the agent table", async () => {
    const { container } = renderView(<Settings />);
    expect((await screen.findAllByText("Models and keys")).length).toBeGreaterThan(0);
    // The key table is what this tab is for; the other four tabs are reachable from here.
    await waitFor(() => expect(container.textContent).toMatch(/Mistral/));
    expect(container.textContent).toMatch(/\*\*\*\*abcd/);
    for (const tab of ["Agents", "Godot", "History and identity", "Notifications"]) {
      expect(screen.getAllByText(tab).length).toBeGreaterThan(0);
    }
    expectNoLeakedInternals(container);
  });
});
