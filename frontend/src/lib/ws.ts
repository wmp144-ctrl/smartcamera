/**
 * Live event stream.
 *
 * A single WebSocket is shared by the whole app; components subscribe to the
 * topics they care about. Reconnects use exponential backoff and the socket is
 * closed cleanly when the last subscriber leaves, so React StrictMode's double
 * mount cannot leak connections.
 */

import { useEffect, useRef } from "react";
import { WS_URL, type WsMessage } from "./api";

export type Topic = "event" | "alert" | "status" | "detection" | "stats" | "system";

export const ALL_TOPICS: Topic[] = ["event", "alert", "status", "detection", "stats", "system"];

export type ConnectionState = "connecting" | "open" | "closed";

export interface StreamMessage {
  topic: Topic;
  ts: string;
  data: Record<string, unknown>;
}

type Listener = (message: StreamMessage) => void;
type StateListener = (state: ConnectionState) => void;

class EventStream {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private stateListeners = new Set<StateListener>();
  private retries = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private explicitlyClosed = false;

  state: ConnectionState = "closed";
  lastMessageAt: string | null = null;

  subscribe(listener: Listener, onState?: StateListener): () => void {
    this.listeners.add(listener);
    if (onState) {
      this.stateListeners.add(onState);
      onState(this.state);
    }
    this.open();
    return () => {
      this.listeners.delete(listener);
      if (onState) this.stateListeners.delete(onState);
      if (this.listeners.size === 0) this.close();
    };
  }

  private setState(state: ConnectionState) {
    this.state = state;
    for (const listener of this.stateListeners) listener(state);
  }

  private open() {
    if (typeof window === "undefined") return;
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.explicitlyClosed = false;
    this.setState("connecting");

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}${WS_URL}?replay=15`;
    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.retries = 0;
      this.setState("open");
    };
    socket.onmessage = (raw) => {
      let payload: WsMessage;
      try {
        payload = JSON.parse(raw.data as string) as WsMessage;
      } catch {
        return;
      }
      if (payload.type !== "message" || !payload.topic) return;
      this.lastMessageAt = payload.ts ?? new Date().toISOString();
      const message: StreamMessage = {
        topic: payload.topic as Topic,
        ts: this.lastMessageAt,
        data: (payload.data ?? {}) as Record<string, unknown>,
      };
      for (const listener of this.listeners) listener(message);
    };
    socket.onerror = () => {
      /* onclose handles recovery */
    };
    socket.onclose = () => {
      this.socket = null;
      this.setState("closed");
      if (!this.explicitlyClosed) this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    if (this.timer) clearTimeout(this.timer);
    const delay = Math.min(15000, 800 * 2 ** Math.min(this.retries, 5));
    this.retries += 1;
    this.timer = setTimeout(() => this.open(), delay);
  }

  private close() {
    this.explicitlyClosed = true;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.socket?.close();
    this.socket = null;
    this.setState("closed");
  }
}

export const eventStream = new EventStream();

/** Subscribe to topics for the lifetime of a component. */
export function useLiveStream(
  topics: Topic[],
  handler: (message: StreamMessage) => void,
  onState?: (state: ConnectionState) => void,
): void {
  // Keep the latest callbacks in refs: the socket subscription itself must only
  // churn when the *set of topics* changes, not on every render.
  const handlerRef = useRef(handler);
  const stateRef = useRef(onState);
  handlerRef.current = handler;
  stateRef.current = onState;

  const key = topics.join(",");
  useEffect(() => {
    const wanted = new Set(key.split(",") as Topic[]);
    return eventStream.subscribe(
      (message) => {
        if (wanted.has(message.topic)) handlerRef.current(message);
      },
      (state) => stateRef.current?.(state),
    );
  }, [key]);
}
