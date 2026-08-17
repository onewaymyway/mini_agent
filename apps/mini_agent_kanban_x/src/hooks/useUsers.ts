import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createUser, listUsers, removeUser, rotateUserToken, updateUser } from "../api/endpoints";
import { useAuthStore } from "../stores/authStore";

export function useUsers() {
  const token = useAuthStore((s) => s.token);
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["users"] });

  const users = useQuery({
    queryKey: ["users"],
    queryFn: listUsers,
    enabled: !!token,
  });

  const create = useMutation({ mutationFn: createUser, onSuccess: invalidate });
  const update = useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: { role?: string; meta?: Record<string, unknown> } }) =>
      updateUser(userId, body),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: (userId: string) => removeUser(userId), onSuccess: invalidate });
  const rotateToken = useMutation({ mutationFn: (userId: string) => rotateUserToken(userId) });

  return { users, create, update, remove, rotateToken };
}
