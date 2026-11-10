import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteAgent, getAgent, listAgents } from "./api";

export function useAgents() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(),
  });
  return { agents: data ?? [], isLoading, error };
}

export function useAgent(
  name: string | null | undefined,
  options: { preventAutomaticErrorRecovery?: boolean } = {},
) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["agents", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
    ...(options.preventAutomaticErrorRecovery
      ? {
          retryOnMount: false,
          refetchOnWindowFocus: false,
          refetchOnReconnect: false,
        }
      : {}),
  });
  return { agent: data ?? null, isLoading, error, refetch };
}

export function useDeleteAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteAgent(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}
