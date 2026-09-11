/**
 * The one place the UI talks to the backend.
 *
 * Two rules, both learned from the API's own shape:
 *
 * 1. **Relative URLs only.** The Tauri shell serves this bundle from the same origin as the
 *    Python process, so `/api/...` is correct in the packaged app, in `vite dev` (proxied) and
 *    in a browser pointed at the backend. A `localhost:8787` literal anywhere would break the
 *    packaged build.
 * 2. **Errors are data.** The backend returns `{error, message, action}`, so an `ApiError`
 *    carries the machine code and the sentence written for the user. Components show the
 *    message; only the connection code switches on `action`, to open the right screen.
 */

export type ApiErrorBody = {
  error: string;
  message: string;
  action?: string;
  problems?: string[];
  [key: string]: unknown;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly action?: string;
  readonly problems: string[];
  readonly body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error || "unknown";
    this.action = body.action;
    this.problems = body.problems ?? [];
    this.body = body;
  }

  /** True when the studio simply has no project open yet, which is a normal first-run state. */
  get needsProject(): boolean {
    return this.code === "no_active_project";
  }

  get isConnectionProblem(): boolean {
    return this.status === 0;
  }
}

export const API_BASE = "";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    // The sidecar is not answering. This is the most common real-world failure (it is still
    // starting, or it crashed), so it gets its own code and a plain-language sentence.
    throw new ApiError(0, {
      error: "backend_unreachable",
      message:
        "PulseG Studio's background service is not answering. It may still be starting up - wait a moment and try again.",
      detail: String(cause),
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let payload: unknown = undefined;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { error: "invalid_response", message: text.slice(0, 300) };
    }
  }

  if (!response.ok) {
    const body =
      payload && typeof payload === "object" && !Array.isArray(payload)
        ? (payload as ApiErrorBody)
        : { error: "request_failed", message: `Request failed with status ${response.status}` };
    throw new ApiError(response.status, body);
  }

  return payload as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
};

/** Build a query string, skipping empty values so URLs stay readable in the logs. */
export function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
