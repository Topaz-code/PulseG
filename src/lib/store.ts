import { create } from "zustand";

/**
 * Small, deliberate client state.
 *
 * Anything the server owns - tasks, agents, projects, logs - lives in React Query, because the
 * server is the source of truth and the event stream invalidates it. What lives here is only
 * what the server does not know about: which drawer is open, what the user is typing in the
 * command bar, and which preview mode they picked.
 */

export type PreviewMode = "graph" | "game";

type DialogName = "setup" | "task" | "settings" | "newProject" | "reference" | null;

type StudioState = {
  /** The task drawer, keyed by task id so the drawer can be restored after a refresh. */
  openTaskId: string;
  openTask: (taskId: string) => void;
  closeTask: () => void;

  dialog: DialogName;
  dialogPayload: Record<string, unknown>;
  openDialog: (name: Exclude<DialogName, null>, payload?: Record<string, unknown>) => void;
  closeDialog: () => void;

  /** Command bar draft. Kept here so switching views does not lose half-written instructions. */
  draft: string;
  setDraft: (value: string) => void;
  draftMeta: {
    genre: string;
    artStyle: string;
    perspective: string;
    godotVersion: string;
    reference: string;
  };
  setDraftMeta: (patch: Partial<StudioState["draftMeta"]>) => void;

  previewMode: PreviewMode;
  setPreviewMode: (mode: PreviewMode) => void;

  railOpen: boolean;
  toggleRail: () => void;
  setRailOpen: (open: boolean) => void;

  /** Toast notifications, one at a time, because two overlapping toasts is a design smell. */
  toast: { title: string; body?: string; tone: "info" | "success" | "danger" } | null;
  showToast: (toast: StudioState["toast"]) => void;
  clearToast: () => void;
};

export const useStudio = create<StudioState>((set, get) => ({
  openTaskId: "",
  openTask: (taskId) => set({ openTaskId: taskId }),
  closeTask: () => set({ openTaskId: "" }),

  dialog: null,
  dialogPayload: {},
  openDialog: (name, payload = {}) => set({ dialog: name, dialogPayload: payload }),
  closeDialog: () => set({ dialog: null, dialogPayload: {} }),

  draft: "",
  setDraft: (value) => set({ draft: value }),
  draftMeta: { genre: "Auto", artStyle: "Auto", perspective: "Auto", godotVersion: "Auto", reference: "" },
  setDraftMeta: (patch) => set({ draftMeta: { ...get().draftMeta, ...patch } }),

  previewMode: "graph",
  setPreviewMode: (mode) => set({ previewMode: mode }),

  railOpen: true,
  toggleRail: () => set({ railOpen: !get().railOpen }),
  setRailOpen: (open) => set({ railOpen: open }),

  toast: null,
  showToast: (toast) => set({ toast }),
  clearToast: () => set({ toast: null }),
}));
