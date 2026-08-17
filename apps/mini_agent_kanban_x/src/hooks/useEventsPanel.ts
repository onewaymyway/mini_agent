import { useEffect, useRef, useState } from "react";
import { getEvents } from "../api/endpoints";
import type { AgentEventPayload } from "../api/types";

/**
 * 事件流面板：轮询 /v1/events 做增量拉取（对应旧看板 `_fetch_events_incremental`）。
 * 本地维护一个上限为 cacheCap 的滚动缓存，避免长时间挂着页面导致内存无限增长。
 */
export function useEventsPanel(sessionId: string | undefined, cacheCap = 300) {
  const [events, setEvents] = useState<AgentEventPayload[]>([]);
  const sinceRef = useRef<string | undefined>(undefined);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    setEvents([]);
    sinceRef.current = undefined;
    let cancelled = false;

    async function poll() {
      try {
        const res = await getEvents(sessionId, sinceRef.current, 100);
        const items = res.events || [];
        if (items.length && !cancelled) {
          setEvents((prev) => {
            const next = [...prev, ...items];
            return next.length > cacheCap ? next.slice(next.length - cacheCap) : next;
          });
          const lastId = items[items.length - 1]?.id;
          if (lastId) sinceRef.current = String(lastId);
        }
      } catch {
        // 忽略单次轮询失败，下一轮继续
      }
      if (!cancelled) timerRef.current = window.setTimeout(poll, 3000);
    }
    poll();

    return () => {
      cancelled = true;
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [sessionId, cacheCap]);

  return { events, clear: () => setEvents([]) };
}
