import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getSelfConfig, patchSelfConfig } from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useConfig() {
  const token = useAuthStore((s) => s.token);
  const queryClient = useQueryClient();

  const config = useQuery({
    queryKey: ["self-config"],
    queryFn: getSelfConfig,
    enabled: !!token,
  });

  const patch = useMutation({
    mutationFn: (updates: { json_key: string; value: unknown }[]) => patchSelfConfig(updates),
    onSuccess: (data) => {
      queryClient.setQueryData(["self-config"], data);
    },
  });

  return { config, patch };
}
