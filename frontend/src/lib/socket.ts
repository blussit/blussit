import { useEffect, useRef } from "react";
import { API_BASE_URL, tokenStorage } from "./api-client";

/**
 * One shared WebSocket connection for the whole authenticated app —
 * components subscribe to named "channels" (see backend's ws_routes.py for
 * the exact channel formats/authorization) rather than each opening their
 * own socket. Every message the server pushes is either a small
 * `{type: "changed", ...}` ping (the handler should refetch/invalidate,
 * never trust it as the real data) or, for captain-location specifically,
 * the actual `{type: "captain_location", latitude, longitude, ...}`
 * payload — see the backend's ws_manager.py module docstring for why that
 * split exists.
 *
 * Auto-reconnects with backoff and re-subscribes to every channel a
 * component is still mounted and asking for — a dropped connection never
 * needs a manual "refresh the page" recovery. Every consumer of this
 * should still keep a much-longer-than-before fallback poll interval
 * alongside its subscription (see SlotPicker/BookingQueuePage/etc.) —
 * sockets do drop, and a stale UI is worse than one redundant fetch.
 */

type Message = Record<string, unknown> & { type: string; channel?: string };
type Handler = (message: Message) => void;

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

class LiveSocket {
  private ws: WebSocket | null = null;
  private channelHandlers = new Map<string, Set<Handler>>();
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByUs = false;

  private wsUrl(): string | null {
    const token = tokenStorage.getAccess();
    if (!token) return null;
    // API_BASE_URL looks like "http://host:port/api/v1" (or https in
    // production) — swap the scheme and point at the /ws route under the
    // same prefix, so this never drifts from wherever the API actually is.
    const httpUrl = new URL(API_BASE_URL, window.location.origin);
    const wsScheme = httpUrl.protocol === "https:" ? "wss:" : "ws:";
    return `${wsScheme}//${httpUrl.host}${httpUrl.pathname.replace(/\/$/, "")}/ws?token=${encodeURIComponent(token)}`;
  }

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    const url = this.wsUrl();
    if (!url) return; // not logged in — nothing to connect for yet
    this.closedByUs = false;

    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.reconnectAttempt = 0;
      // Re-subscribe to everything a mounted component still wants — a
      // fresh connection has none of the server-side subscriptions the
      // previous one had.
      for (const channel of this.channelHandlers.keys()) {
        this.send({ action: "subscribe", channel });
      }
    };

    ws.onmessage = (event) => {
      let message: Message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      const channel = message.channel;
      if (!channel) return;
      const handlers = this.channelHandlers.get(channel);
      if (!handlers) return;
      for (const handler of handlers) handler(message);
    };

    ws.onclose = () => {
      this.ws = null;
      if (this.closedByUs) return;
      this.scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose fires right after in browsers — reconnect logic lives there.
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this.reconnectAttempt, RECONNECT_MAX_MS);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private send(payload: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
    // If not open yet, onopen's re-subscribe loop covers it once connected —
    // nothing to queue here.
  }

  subscribe(channel: string, handler: Handler) {
    let handlers = this.channelHandlers.get(channel);
    const isNewChannel = !handlers;
    if (!handlers) {
      handlers = new Set();
      this.channelHandlers.set(channel, handlers);
    }
    handlers.add(handler);
    this.connect();
    if (isNewChannel) this.send({ action: "subscribe", channel });
  }

  unsubscribe(channel: string, handler: Handler) {
    const handlers = this.channelHandlers.get(channel);
    if (!handlers) return;
    handlers.delete(handler);
    if (handlers.size === 0) {
      this.channelHandlers.delete(channel);
      this.send({ action: "unsubscribe", channel });
    }
  }

  /** Called on logout — drops the connection entirely so a stale token
   * never lingers in an open socket, and stops all reconnect attempts. */
  disconnect() {
    this.closedByUs = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.channelHandlers.clear();
    this.ws?.close();
    this.ws = null;
  }
}

export const liveSocket = new LiveSocket();

/**
 * Subscribe to a channel for the lifetime of the calling component.
 * Pass `null`/`undefined` as the channel to skip subscribing (e.g. while
 * an id the channel depends on hasn't loaded yet) without needing a
 * conditional hook call.
 */
export function useLiveChannel(channel: string | null | undefined, onMessage: Handler) {
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    if (!channel) return;
    const wrapped: Handler = (message) => handlerRef.current(message);
    liveSocket.subscribe(channel, wrapped);
    return () => liveSocket.unsubscribe(channel, wrapped);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel]);
}
