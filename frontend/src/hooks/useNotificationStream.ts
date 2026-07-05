import { useEffect, useRef } from 'react';
import { useAuthStore } from '../store/slices/authSlice';
import { useNotificationStore } from '../store/notificationStore';
import type { NotificationEvent } from '../services/notificationService';

/**
 * Real-time notification stream.
 *
 * Opens a WebSocket to ``/ws/notifications`` (per-user channel), authenticated
 * via the ``["bearer", <jwt>]`` Sec-WebSocket-Protocol subprotocol (B11 — the
 * token stays out of URL logs). Incoming ``notification`` messages are pushed
 * into the notification store; the bell re-renders instantly. Falls back to a
 * 30s unread-count poll if the socket cannot be opened (e.g. Redis down).
 *
 * Mount once for the whole authenticated session (Layout does this) — it is
 * intentionally NOT patient-scoped, so system/tenant notifications surface
 * even when no patient is selected.
 */
const RECONNECT_BACKOFF_MS = 5000;
const FALLBACK_POLL_MS = 30000;

export function useNotificationStream() {
  const token = useAuthStore((s) => s.token);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const onLiveNotification = useNotificationStore((s) => s.onLiveNotification);
  const setConnected = useNotificationStore((s) => s.setConnected);
  const refreshUnreadCount = useNotificationStore((s) => s.refreshUnreadCount);

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const stoppedRef = useRef(false);
  // Debounces the actual WS construction so React StrictMode's dev
  // mount→unmount→remount cycle doesn't open a socket only to abort it
  // mid-handshake (which logs "Firefox can't establish a connection" /
  // NS_BINDING_ABORTED). The cleanup clears this timer, so the first
  // (immediately-discarded) mount never opens a real connection.
  const connectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!isAuthenticated || !token) return;
    stoppedRef.current = false;

    const buildUrl = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${proto}//${window.location.host}/api/v1/ws/notifications`;
    };

    const connect = () => {
      if (stoppedRef.current) return;
      let socket: WebSocket;
      try {
        socket = new WebSocket(buildUrl(), ['bearer', token]);
      } catch {
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        setConnected(true);
        clearFallback();
        // Sync the canonical unread count on connect.
        refreshUnreadCount();
      };

      socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'notification' && msg.notification) {
            onLiveNotification(msg.notification as NotificationEvent);
          } else if (msg.type === 'unread_count') {
            useNotificationStore.setState({ unreadCount: msg.count ?? 0 });
          }
          // ping messages are ignored
        } catch {
          // Ignore malformed frames
        }
      };

      socket.onclose = () => {
        setConnected(false);
        socketRef.current = null;
        if (!stoppedRef.current) {
          scheduleReconnect();
          startFallback();
        }
      };

      socket.onerror = () => {
        // Let onclose handle reconnect.
        try {
          socket.close();
        } catch {
          // noop
        }
      };
    };

    const scheduleConnect = () => {
      if (connectTimer.current) return;
      connectTimer.current = setTimeout(() => {
        connectTimer.current = null;
        connect();
      }, 100);
    };

    const scheduleReconnect = () => {
      if (reconnectTimer.current) return;
      reconnectTimer.current = setTimeout(() => {
        reconnectTimer.current = null;
        connect();
      }, RECONNECT_BACKOFF_MS);
    };

    const startFallback = () => {
      if (pollTimer.current) return;
      refreshUnreadCount();
      pollTimer.current = setInterval(() => refreshUnreadCount(), FALLBACK_POLL_MS);
    };

    const clearFallback = () => {
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
    };

    scheduleConnect();

    return () => {
      stoppedRef.current = true;
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (connectTimer.current) {
        clearTimeout(connectTimer.current);
        connectTimer.current = null;
      }
      clearFallback();
      if (socketRef.current) {
        try {
          socketRef.current.close();
        } catch {
          // noop
        }
        socketRef.current = null;
      }
      setConnected(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, token]);
}
