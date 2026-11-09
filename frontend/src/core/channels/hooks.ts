// frontend/src/core/channels/hooks.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteChannelConfig,
  getChannelConfig,
  listChannelConfigs,
  updateChannelConfig,
} from "./api";
import type { ChannelConfigUpdate } from "./types";

export function useChannelConfigs() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["channelConfigs"],
    queryFn: () => listChannelConfigs(),
  });
  return { configs: data?.configs ?? [], isLoading, error };
}

export function useUpdateChannelConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      channelType,
      config,
    }: {
      channelType: string;
      config: ChannelConfigUpdate;
    }) => {
      await updateChannelConfig(channelType, config);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["channelConfigs"] });
    },
  });
}

export function useDeleteChannelConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (channelType: string) => {
      await deleteChannelConfig(channelType);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["channelConfigs"] });
    },
  });
}
