/**
 * The planning rail, which is the human end of the /grillme intake.
 *
 * Two states matter and both are on screen: an answer that was read (say what was understood) and
 * an answer that was not (say why, and offer the fix). The second one is the reason this test
 * exists - the intake reads prose through a model, so with no key configured the checklist simply
 * does not move, and the rail has to make that legible rather than look broken.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ContextRail } from "@/components/layout/ContextRail";
import { installFetch, stubFixture } from "@/test/fixtures";

let restore: () => void;

beforeEach(() => {
  restore = installFetch();
});

afterEach(() => {
  restore();
});

/** Put the planning state's extraction report into the state this test is about. */
function setExtraction(extraction: Record<string, unknown>) {
  stubFixture("/api/planning/state", { extraction });
}

function renderRail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ContextRail />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ContextRail", () => {
  it("shows the design checklist and the questions still open", async () => {
    const { container } = renderRail();
    expect(await screen.findByText("Design checklist")).toBeTruthy();
    expect(container.textContent).toMatch(/Core mechanics have rules/);
    // The ledger is incomplete in the fixture, so the gate is closed and says why.
    expect(await screen.findByText("Still asking")).toBeTruthy();
    expect(container.textContent).toMatch(/Who does the keeper meet\?/);
  });

  it("says what it read from the last answer", async () => {
    setExtraction({ ok: true, changed: ["mechanics", "art_direction"], at: "2026-09-11T10:05:00+00:00" });
    const { container } = renderRail();
    expect(await screen.findByText("Read from your last answer")).toBeTruthy();
    expect(container.textContent).toMatch(/Mechanics/);
    expect(container.textContent).toMatch(/Art direction/i);
  });

  it("says why nothing was read, and offers the key screen", async () => {
    setExtraction({
      ok: false,
      reason: "No provider with a key could be reached, so the answer was stored but not read.",
      changed: [],
      needs_key: true,
      at: "2026-09-11T10:05:00+00:00",
    });
    const { container } = renderRail();
    expect(await screen.findByText("Nothing was read from your last answer")).toBeTruthy();
    expect(container.textContent).toMatch(/No provider with a key could be reached/);
    expect(screen.getByText("Add a provider key")).toBeTruthy();
  });

  it("stays quiet when a read succeeded and changed nothing", async () => {
    setExtraction({ ok: true, changed: [], at: "2026-09-11T10:05:00+00:00" });
    const { container } = renderRail();
    await waitFor(() => expect(container.textContent).toMatch(/Design checklist/));
    expect(container.textContent).not.toMatch(/Read from your last answer/);
    expect(container.textContent).not.toMatch(/Nothing was read/);
  });
});
