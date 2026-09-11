import { api } from "./api";

/**
 * The handful of things that only work when this is running as the real desktop app.
 *
 * In the packaged build, PulseG Studio is a Tauri window, so "open this folder" can actually open
 * Explorer and "show me the Godot window" can actually raise a window. In a browser - which is how
 * the development preview runs - those calls are not available, and pretending otherwise would be
 * the exact "localhost demo" behaviour this project is trying to avoid. So each helper does the
 * real thing when it can and says what happened when it cannot.
 */

type TauriBridge = {
  core?: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
};

function bridge(): TauriBridge | undefined {
  return (globalThis as { __TAURI__?: TauriBridge }).__TAURI__;
}

export function isDesktop(): boolean {
  return Boolean(bridge()?.core?.invoke);
}

export type FolderReveal = {
  /** The absolute path, always returned so the UI can show or copy it. */
  path: string;
  /** True only when the operating system actually opened a window for it. */
  opened: boolean;
  detail: string;
};

/**
 * Ask the sidecar where the folder is, then ask the desktop shell to show it. The path never
 * travels the other way: the frontend does not guess at the filesystem layout.
 */
export async function revealProjectFolder(projectId: string, sub = ""): Promise<FolderReveal> {
  const answer = await api.get<{ path: string }>(
    `/api/projects/${projectId}/open-folder${sub ? `?sub=${encodeURIComponent(sub)}` : ""}`,
  );
  const invoke = bridge()?.core?.invoke;
  if (!invoke) {
    return {
      path: answer.path,
      opened: false,
      detail: "Copy this path and paste it into Explorer. The packaged app opens it for you.",
    };
  }
  try {
    await invoke("open_path", { path: answer.path });
    return { path: answer.path, opened: true, detail: "Opened in Explorer." };
  } catch (error) {
    return {
      path: answer.path,
      opened: false,
      detail: `The shell could not open it: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

/** Copies text, falling back to a prompt-free path when the clipboard is unavailable. */
export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}
