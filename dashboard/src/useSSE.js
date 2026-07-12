import { useEffect, useRef } from "react";
import { SSE_URL } from "./api";

/**
 * Subscribe to the backend SSE stream.
 * Calls onEvent(data) for each message.
 * Reconnects automatically on disconnect.
 */
export function useSSE(onEvent) {
  const esRef    = useRef(null);
  const onEventRef = useRef(onEvent);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    let reconnectTimer;
    let stopped = false;

    function connect() {
      esRef.current?.close();
      const es = new EventSource(SSE_URL);

      es.onmessage = (e) => {
        if (!e.data) return;
        try {
          onEventRef.current(JSON.parse(e.data));
        } catch {
          // Ignore malformed events and keep the stream alive.
        }
      };

      es.onerror = () => {
        es.close();
        if (!stopped) reconnectTimer = setTimeout(connect, 3000);
      };

      esRef.current = es;
    }

    connect();
    return () => {
      stopped = true;
      clearTimeout(reconnectTimer);
      esRef.current?.close();
    };
  }, []);
}
