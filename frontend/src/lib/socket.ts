import axios from "axios";
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
 * The socket exists only while some mounted component wants a channel,
 * and only while the tab is visible: every open socket holds a server
 * slot, so a forgotten background tab must not hold one forever. It
 * reconnects with backoff and re-subscribes on return; the server answers
 * each re-subscribe with a "subscribed" message on that channel, which
 * handlers treat like "changed" and refetch whatever they missed.
 *
 * Every consumer of this should still keep a much-longer-than-before
 * fallback poll interval alongside its subscription (see
 * SlotPicker/BookingQueuePage/etc.) — sockets do drop, and a stale UI is
 * worse than one redundant fetch.
 */

type Message = Record<string, unknown> & { type: string; channel?: string };
type Handler = (message: Message) => void;

const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
// ws_routes.py closes with this when the token is bad or has expired.
const AUTH_CLOSE_CODE = 4401;
// Past this many auth closes in a row, stop until the tab is shown again
// rather than hammering the server with a token it keeps refusing.
const MAX_AUTH_FAILURES = 3;
const IDLE_CLOSE_MS = 10_000;
const HIDDEN_CLOSE_MS = 2 * 60_000;

function tokenExpiresWithin(token: string, seconds: number): boolean {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.exp === "number" && payload.exp * 1000 - Date.now() < seconds * 1000;
  } catch {
    return false;
  }
}

const tabHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

class LiveSocket {
  private ws: WebSocket | null = null;
  private channelHandlers = new Map<string, Set<Handler>>();
  private reconnectAttempt = 0;
  private authFailures = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private hiddenTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshing = false;
  // The token a refresh just handed us: open with it even if the local
  // clock calls it expired (a skewed clock must not refresh in a loop).
  private refreshedTo: string | null = null;
  private listening = false;

  private wsUrl(token: string): string {
    // API_BASE_URL looks like "http://host:port/api/v1" (or https in
    // production) — swap the scheme and point at the /ws route under the
    // same prefix, so this never drifts from wherever the API actually is.
    const httpUrl = new URL(API_BASE_URL, window.location.origin);
    const wsScheme = httpUrl.protocol === "https:" ? "wss:" : "ws:";
    return `${wsScheme}//${httpUrl.host}${httpUrl.pathname.replace(/\/$/, "")}/ws?token=${encodeURIComponent(token)}`;
  }

  private listenForVisibility() {
    if (this.listening || typeof document === "undefined") return;
    this.listening = true;
    document.addEventListener("visibilitychange", () => {
      if (tabHidden()) {
        if (!this.hiddenTimer && this.ws) {
          this.hiddenTimer = setTimeout(() => {
            this.hiddenTimer = null;
            if (tabHidden()) this.drop();
          }, HIDDEN_CLOSE_MS);
        }
        return;
      }
      this.clearTimer("hiddenTimer");
      if (this.channelHandlers.size && !this.ws) {
        this.clearTimer("reconnectTimer");
        this.reconnectAttempt = 0;
        this.authFailures = 0;
        this.connect();
      }
    });
  }

  private clearTimer(name: "reconnectTimer" | "idleTimer" | "hiddenTimer") {
    const timer = this[name];
    if (timer) clearTimeout(timer);
    this[name] = null;
  }

  connect() {
    this.listenForVisibility();
    if (!this.channelHandlers.size || this.refreshing || this.reconnectTimer) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    if (tabHidden()) return; // the visibilitychange listener connects on return
    if (this.authFailures >= MAX_AUTH_FAILURES) return;
    const token = tokenStorage.getAccess();
    if (!token) return; // not logged in — nothing to connect for yet
    if (token !== this.refreshedTo && tokenExpiresWithin(token, 10)) {
      void this.refreshThenReconnect();
      return;
    }
    this.open(token);
  }

  private open(token: string) {
    const ws = new WebSocket(this.wsUrl(token));
    this.ws = ws;

    ws.onopen = () => {
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
      if (message.type === "connected") {
        // Only an authenticated socket gets this — a refused token is
        // accepted and closed with 4401 without it.
        this.reconnectAttempt = 0;
        this.authFailures = 0;
        return;
      }
      const channel = message.channel;
      if (!channel) return;
      const handlers = this.channelHandlers.get(channel);
      if (!handlers) return;
      for (const handler of handlers) handler(message);
    };

    ws.onclose = (event) => {
      if (this.ws !== ws) return;
      this.ws = null;
      // Hidden: leave it closed (no refresh, no retries) — the
      // visibilitychange listener reconnects when the tab is shown.
      if (tabHidden()) return;
      if (event.code === AUTH_CLOSE_CODE) {
        this.authFailures += 1;
        // Another request (or tab) may already hold a fresh token.
        const stored = tokenStorage.getAccess();
        if (stored && stored !== token && !tokenExpiresWithin(stored, 10)) this.scheduleReconnect();
        else void this.refreshThenReconnect();
        return;
      }
      this.scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose fires right after in browsers — reconnect logic lives there.
    };
  }

  /** Same refresh call as api-client's 401 interceptor. Called directly
   * rather than provoked through a 401: the REST layer still accepts a
   * token for up to a second past the `exp` the socket was closed at, so
   * a probe request there can come back 200 and refresh nothing. Refresh
   * tokens aren't single-use, so racing the interceptor is harmless. */
  private async refreshThenReconnect() {
    const refreshToken = tokenStorage.getRefresh();
    if (this.refreshing || !refreshToken) return;
    this.refreshing = true;
    try {
      const { data } = await axios.post(`${API_BASE_URL}/auth/refresh`, { refresh_token: refreshToken });
      tokenStorage.set(data.data.access_token, data.data.refresh_token);
      this.refreshedTo = data.data.access_token;
    } catch (error) {
      // Refused (not just offline): the session is over. Stop here — the
      // next API call's 401 handling sends the user to log in.
      if (axios.isAxiosError(error) && error.response && error.response.status < 500) {
        this.authFailures = MAX_AUTH_FAILURES;
      }
    } finally {
      this.refreshing = false;
    }
    this.scheduleReconnect();
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || !this.channelHandlers.size) return;
    const backoff = Math.min(RECONNECT_BASE_MS * 2 ** this.reconnectAttempt, RECONNECT_MAX_MS);
    // Jitter so every tab doesn't reconnect in the same instant after a
    // server restart.
    const delay = backoff * (0.8 + Math.random() * 0.4);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  /** Close without reconnecting; a later subscribe or tab return reopens. */
  private drop() {
    const ws = this.ws;
    this.ws = null;
    this.clearTimer("reconnectTimer");
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      ws.onopen = null;
      ws.close();
    }
  }

  private send(payload: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
    // If not open yet, onopen's re-subscribe loop covers it once connected —
    // nothing to queue here.
  }

  subscribe(channel: string, handler: Handler) {
    this.clearTimer("idleTimer");
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
    // Short grace so a route change (unmount, then the next page's mount)
    // doesn't tear down and rebuild the connection.
    if (!this.channelHandlers.size && !this.idleTimer) {
      this.idleTimer = setTimeout(() => {
        this.idleTimer = null;
        if (!this.channelHandlers.size) this.drop();
      }, IDLE_CLOSE_MS);
    }
  }

  /** Called on logout — drops the connection entirely so a stale token
   * never lingers in an open socket, and stops all reconnect attempts. */
  disconnect() {
    this.clearTimer("idleTimer");
    this.clearTimer("hiddenTimer");
    this.channelHandlers.clear();
    this.drop();
    this.reconnectAttempt = 0;
    this.authFailures = 0;
    this.refreshedTo = null;
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
