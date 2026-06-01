import { useEffect, useRef, useCallback } from "react";
import { SSE_URL } from "./api";

/**
 * Subscribe to the backend SSE stream.
 * Calls onEvent(data) for each message.
 * Reconnects automatically on disconnect.
 */
export function useSSE(onEvent) {
  const esRef    = useRef(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const connect = useCallback(() => {
    if (esRef.current) esRef.current.close();
    const es = new EventSource(SSE_URL);

    es.onmessage = (e) => {
      if (!e.data || e.data.startsWith(":")) return;
      try {
        const data = JSON.parse(e.data);
        onEventRef.current(data);
      } catch {}
    };

    es.onerror = () => {
      es.close();
      // Reconnect after 3s
      setTimeout(connect, 3000);
    };

    esRef.current = es;
  }, []);

  useEffect(() => {
    connect();
    return () => esRef.current?.close();
  }, [connect]);
}
