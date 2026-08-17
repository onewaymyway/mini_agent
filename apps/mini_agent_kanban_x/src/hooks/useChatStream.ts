import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getHistory, postChat, postInterrupt } from "../api/endpoints";
import { subscribeSse } from "../api/sse";
import type { HistoryItem } from "../api/types";

interface StreamMessage extends HistoryItem {
  pending?: boolean;
}

/**
 * 聊天流式对话：
 *  1. `history` query 拉取历史记录做首屏渲染；
 *  2. 发送消息后拿到 turn_id，订阅 /v1/stream/{turn_id} 做增量 token 拼接；
 *  3. 收到 turn 结束事件后 invalidate history，保证和后端最终存储的记录一致。
 *
 * 这是本次重构里对旧 Streamlit 版本"轮询 + st.rerun() 整页重绘"影响最大的
 * 一处能力：这里只有当前这条消息的文本节点会更新，不会带动会话列表/侧边栏
 * 等其它组件重新渲染。
 */
export function useChatStream(sessionId?: string) {
  const qc = useQueryClient();
  const [liveText, setLiveText] = useState("");
  const [turnId, setTurnId] = useState<string | null>(null);
  const closeRef = useRef<(() => void) | null>(null);

  const history = useQuery({
    queryKey: ["history", sessionId],
    queryFn: () => getHistory(sessionId),
  });

  const stopStream = useCallback(() => {
    closeRef.current?.();
    closeRef.current = null;
  }, []);

  const send = useMutation({
    mutationFn: (message: string) => postChat({ message, session_id: sessionId }),
    onSuccess: (res) => {
      setLiveText("");
      const tid = (res as { turn_id?: string }).turn_id || null;
      setTurnId(tid);
      stopStream();
      const path = tid ? `/stream/${encodeURIComponent(tid)}` : "/stream";
      closeRef.current = subscribeSse(path, {
        onMessage: (chunk) => {
          // event-stream 帧格式: "event: xxx\ndata: {...}\nid: ..."
          const dataLine = chunk
            .split("\n")
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trim())
            .join("\n");
          if (!dataLine) return;
          try {
            const payload = JSON.parse(dataLine);
            const delta =
              (payload.delta as string) ||
              (payload.text as string) ||
              (payload.content as string) ||
              "";
            if (delta) setLiveText((prev) => prev + delta);
            const isDone = payload.type === "turn_end" || payload.event === "turn_end" || payload.done;
            if (isDone) {
              stopStream();
              qc.invalidateQueries({ queryKey: ["history", sessionId] });
              qc.invalidateQueries({ queryKey: ["status"] });
            }
          } catch {
            // 非 JSON 数据帧，忽略（心跳/注释行等）
          }
        },
        onError: () => {
          qc.invalidateQueries({ queryKey: ["history", sessionId] });
        },
      });
    },
  });

  const interrupt = useMutation({
    mutationFn: postInterrupt,
    onSuccess: () => {
      stopStream();
      qc.invalidateQueries({ queryKey: ["history", sessionId] });
    },
  });

  useEffect(() => () => stopStream(), [stopStream]);

  const items: StreamMessage[] = [...(history.data?.items || [])];
  if (liveText) items.push({ role: "assistant", content: liveText, pending: true });

  return {
    items,
    isLoadingHistory: history.isLoading,
    send,
    interrupt,
    liveText,
    turnId,
  };
}
