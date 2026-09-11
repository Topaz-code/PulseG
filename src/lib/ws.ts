import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { keys } from "./queries";

/**
 * The live event stream - the reason there is no polling anywhere in this app.
 *
 * The backend publishes every state change on `/ws/events` (task transitions, log lines, agent
 * notes, notifications) and replays recent history on connect, so a window that was closed for
 * an hour catches up instantly instead of showing stale cards.
 *
 * The hook deliberately does two separate jobs:
 *
 * * **Smart invalidation.** A `task_status` event invalidates the board and that task; a
 *   `provider_tested` event invalidates providers. Components keep using plain `useQuery` and
 *   never know a socket exists.
 * * **A live log tail.** Log lines are appended straight into state, because re-fetching a log
 *   for every line would be the polling this design exists to avoid.
 */

export type StudioEvent = {
  seq: number;
  type: string;
  at: string;
  payload: Record<string, unknown>;
};

export type ConnectionState = "connecting" | "open" | "closed";

const REPLAY = 60;

/** Which query keys a given event type makes stale. */
function invalidationsFor(type: string): readonly (readonly unknown[])[] {
  switch (type) {
    case "task_created":
    case "task_status":
    case "task_paused":
    case "task_resumed":
    case "task_rejected":
    case "task_approved":
      return [keys.board, keys.review, ["tasks"], ["run-state"]];
    case "agent_state":
    case "agent_note":
      return [keys.agentStates, keys.agents];
    case "provider_tested":
    case "provider_key_removed":
    case "chain_updated":
      return [keys.providers, keys.agents];
    case "project_created":
    case "project_activated":
    case "project_updated":
      return [keys.projects, keys.activeProject, keys.board];
    case "notification":
    case "notifications_read":
      return [keys.notifications];
    case "milestone":
    case "web_export_ready":
      return [keys.preview, keys.activeProject];
    case "planning_updated":
    case "gdd_updated":
      return [keys.planning, keys.activeProject];
    case "index_rebuilt":
      return [["system", "info"]];
    case "settings_changed":
      return [keys.settings];
    default:
      return [];
  }
}

export function useEventStream(onEvent?: (event: StudioEvent) => void) {
  const client = useQueryClient();
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [lastEvent, setLastEvent] = useState<StudioEvent | null>(null);
  const [logTail, setLogTail] = useState<StudioEvent[]>([]);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry = 0;
    let closed = false;
    let timer: number | undefined;

    const connect = () => {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${protocol}://${window.location.host}/ws/events?replay=${REPLAY}`;
      setConnection("connecting");
      try {
        socket = new WebSocket(url);
      } catch {
        scheduleRetry();
        return;
      }

      socket.onopen = () => {
        retry = 0;
        setConnection("open");
      };

      socket.onmessage = (message) => {
        let event: StudioEvent;
        try {
          event = JSON.parse(message.data as string) as StudioEvent;
        } catch {
          return;
        }
        if (!event.type) return;
        if (event.type === "hello" || event.type === "ping") {
          setConnection("open");
          return;
        }
        setLastEvent(event);
        if (event.type === "log") {
          setLogTail((previous) => [...previous, event].slice(-600));
        }
        for (const key of invalidationsFor(event.type)) {
          void client.invalidateQueries({ queryKey: key });
        }
        handler.current?.(event);
      };

      socket.onerror = () => setConnection("closed");
      socket.onclose = () => {
        setConnection("closed");
        if (!closed) scheduleRetry();
      };
    };

    const scheduleRetry = () => {
      retry += 1;
      // Back off to a slow heartbeat: a desktop app can sit idle for hours, and a tight retry
      // loop against a stopped sidecar is just noise in the log file.
      const delay = Math.min(15_000, 500 * 2 ** Math.min(retry, 5));
      timer = window.setTimeout(connect, delay);
    };

    connect();
    return () => {
      closed = true;
      if (timer) window.clearTimeout(timer);
      socket?.close();
    };
  }, [client]);

  return { connection, lastEvent, logTail, clearLogTail: () => setLogTail([]) };
}
