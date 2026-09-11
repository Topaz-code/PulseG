/**
 * The Setup Wizard, which is the first thing a new user ever sees.
 *
 * "The Setup Wizard runs" is an acceptance criterion, so it gets a test. What matters here is not
 * that boxes render: it is that the wizard reads the first-run state, cannot be finished without a
 * projects folder, and asks the backend for Godot the way the backend's route actually works -
 * that last one was a real defect. The button used to POST to a GET-only route, and the API's 405
 * surfaced as nothing at all.
 *
 * The dialog renders through a portal, so the assertions read `document.body` rather than the
 * container the wizard was mounted into.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetupWizard } from "@/components/SetupWizard";
import { installFetch, stubFixture } from "@/test/fixtures";

let restore: () => void;

beforeEach(() => {
  restore = installFetch();
});

afterEach(() => {
  restore();
});

const pageText = () => document.body.textContent ?? "";

function renderWizard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  const onFinished = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SetupWizard open onFinished={onFinished} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onFinished };
}

describe("SetupWizard", () => {
  it("opens on the Godot step and names all four steps", async () => {
    renderWizard();
    expect((await screen.findAllByText("Welcome to PulseG Studio")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Where Godot lives").length).toBeGreaterThan(0);
    for (const step of ["Where games are saved", "Who commits the work", "A model to build with"]) {
      expect(screen.getAllByText(step).length).toBeGreaterThan(0);
    }
    // The machine already has one, so the wizard says so instead of asking again.
    await waitFor(() => expect(pageText()).toMatch(/Already detected/));
  });

  it("fills the projects folder from the first-run state", async () => {
    renderWizard();
    await screen.findAllByText("Welcome to PulseG Studio");
    fireEvent.click(screen.getByText("Continue"));
    await waitFor(() => expect(pageText()).toMatch(/Every project is a normal folder/));
    const folder = screen.getByLabelText("Projects folder") as HTMLInputElement;
    await waitFor(() => expect(folder.value).toBe("/tmp/pulseg-test/projects"));
  });

  it("asks the backend about Godot over GET, and uses the answer", async () => {
    const calls: Array<{ method: string; url: string }> = [];
    const underlying = globalThis.fetch;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      calls.push({ method: init?.method ?? "GET", url });
      return underlying(input, init);
    }) as typeof fetch;

    renderWizard();
    await screen.findAllByText("Welcome to PulseG Studio");
    fireEvent.click(screen.getByText("Find Godot on this computer"));

    await waitFor(() => expect(calls.some((call) => call.url.includes("/api/settings/godot/detect"))).toBe(true));
    const detect = calls.find((call) => call.url.includes("/api/settings/godot/detect"));
    // GET, because that is the route the API registers. A POST here returns 405 and does nothing.
    expect(detect?.method).toBe("GET");
    await waitFor(() =>
      expect((screen.getByLabelText("Godot executable path") as HTMLInputElement).value).toBe("/usr/bin/godot"),
    );
  });

  it("will not finish without a projects folder", async () => {
    stubFixture("/api/system/first-run", { projects_root: "" });
    const { onFinished } = renderWizard();
    await screen.findAllByText("Welcome to PulseG Studio");

    for (let step = 0; step < 3; step += 1) {
      fireEvent.click(screen.getByText("Continue"));
    }
    await waitFor(() => expect(screen.getByText("Finish setup")).toBeTruthy());
    const finish = screen.getByText("Finish setup").closest("button") as HTMLButtonElement;
    expect(finish.disabled).toBe(true);
    expect(onFinished).not.toHaveBeenCalled();
  });
});
