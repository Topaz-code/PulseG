import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "./lib/api";
import { installCssVariables } from "./design/tokens";
import { App } from "./App";
import "./index.css";

/**
 * The frontend entry point.
 *
 * Three things happen before the first render, and each of them matters to how the app behaves:
 *
 * * **The palette is installed as CSS variables.** Tailwind covers normal styling, but the graph,
 *   the log colours and a few inline styles need the raw values, and this is the one place they
 *   come from - so a colour can never be invented outside the generated token file.
 * * **React Query is configured for a desktop app, not a website.** A user can leave this window
 *   open for hours beside a running build, so queries refetch on connection recovery but not
 *   every time the window regains focus (that would spam the sidecar), and a failure caused by
 *   "no project is open" is not retried - it is a normal state during setup.
 * * **React StrictMode stays on**, because the double-render in development is what catches effect
 *   bugs before they become "the socket opens twice" in a shipped build.
 */
installCssVariables();

const client = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
      retry: (count, error) => {
        if (error instanceof ApiError && (error.needsProject || error.status === 409)) return false;
        return count < 2;
      },
    },
    mutations: {
      retry: 0,
    },
  },
});

const container = document.getElementById("root");
if (!container) {
  throw new Error("The window is missing its root element - the build is incomplete.");
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
