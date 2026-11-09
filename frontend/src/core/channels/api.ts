// frontend/src/core/channels/api.ts
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  ChannelConfigListResponse,
  ChannelConfigResponse,
  ChannelConfigUpdate,
} from "./types";

export async function listChannelConfigs(): Promise<ChannelConfigListResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/user-channels/`);
  if (!response.ok)
    throw new Error(`Failed to fetch channel configs: ${response.statusText}`);
  return response.json();
}

export async function getChannelConfig(
  channelType: string,
): Promise<ChannelConfigResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/user-channels/${encodeURIComponent(channelType)}`,
  );
  if (!response.ok)
    throw new Error(`Failed to fetch channel config: ${response.statusText}`);
  return response.json();
}

export async function updateChannelConfig(
  channelType: string,
  config: ChannelConfigUpdate,
): Promise<ChannelConfigResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/user-channels/${encodeURIComponent(channelType)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    },
  );
  if (!response.ok)
    throw new Error(`Failed to update channel config: ${response.statusText}`);
  return response.json();
}

export async function deleteChannelConfig(channelType: string): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/user-channels/${encodeURIComponent(channelType)}`,
    { method: "DELETE" },
  );
  if (!response.ok)
    throw new Error(`Failed to delete channel config: ${response.statusText}`);
}
