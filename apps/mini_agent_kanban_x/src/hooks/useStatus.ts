import { useQuery } from "@tanstack/react-query";
import { getStatus, getWhoami } from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

/**
 * 状态轮询：相比 Streamlit 版本每次交互都整页重跑，这里只有这一个 Query
 * 会按 `refetchInterval` 轮询，其他组件通过 `useQuery` 命中缓存，不产生新请求。
 * 后续接入 SSE 后，可以把 refetchInterval 调大甚至关闭，改为 SSE 事件触发
 * `queryClient.invalidateQueries(["status"])`。
 */
export function useStatus(sessionId?: string) {
  const token = useAuthStore((s) => s.token);
  return useQuery({
    queryKey: ["status", sessionId],
    queryFn: () => getStatus(sessionId),
    enabled: !!token,
    refetchInterval: 3000,
    retry: 1,
  });
}

export function useWhoami() {
  const token = useAuthStore((s) => s.token);
  return useQuery({
    queryKey: ["whoami"],
    queryFn: getWhoami,
    enabled: !!token,
    retry: 1,
    staleTime: 60_000,
  });
}
