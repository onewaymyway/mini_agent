import { apiBaseUrl } from "./client";
import { useAuthStore } from "../stores/authStore";

export interface SseHandlers {
  onMessage?: (raw: string, lastEventId: string | null) => void;
  onOpen?: () => void;
  onError?: (err: unknown) => void;
}

/**
 * 对后端 GET /v1/stream (或 /v1/stream/{turn_id}) 的轻量封装。
 *
 * 浏览器原生 EventSource 不支持自定义请求头（无法带 Authorization），
 * 而看板的鉴权是 Bearer Token，所以这里用 fetch + ReadableStream 手动解析
 * text/event-stream，而不是用 `new EventSource(url)`。同时实现：
 *   - Last-Event-ID 断线重连（与后端 `_sse_generator` 的重放逻辑对应）
 *   - 指数退避重连（1s → 2s → 4s → 最大 10s）
 *
 * 返回一个 `close()` 函数，组件卸载时务必调用，避免连接泄漏。
 */
export function subscribeSse(path: string, handlers: SseHandlers): () => void {
  let closed = false;
  let backoffMs = 1000;
  let lastEventId: string | null = null;
  let controller: AbortController | null = null;

  async function connect() {
    if (closed) return;
    controller = new AbortController();
    const token = useAuthStore.getState().token;
    const url = apiBaseUrl() + path;
    try {
      const res = await fetch(url, {
        headers: {
          Accept: "text/event-stream",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
        },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`SSE HTTP ${res.status}`);
      handlers.onOpen?.();
      backoffMs = 1000;

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!closed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          const idMatch = chunk.match(/^id: (.*)$/m);
          if (idMatch) lastEventId = idMatch[1];
          handlers.onMessage?.(chunk, lastEventId);
        }
      }
    } catch (err) {
      if (closed) return;
      handlers.onError?.(err);
    }
    if (!closed) {
      setTimeout(connect, backoffMs);
      backoffMs = Math.min(backoffMs * 2, 10000);
    }
  }

  connect();

  return () => {
    closed = true;
    controller?.abort();
  };
}
