// frontend/src/core/channels/types.ts
export interface ChannelCredentials {
  app_id?: string;
  app_secret?: string;
  bot_token?: string;
  [key: string]: string | undefined;
}

export interface ChannelConfigUpdate {
  enabled: boolean;
  credentials: ChannelCredentials;
  config: Record<string, unknown>;
}

export interface ChannelConfigResponse {
  channel_type: string;
  enabled: boolean;
  credentials: Record<string, string>;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ChannelConfigListResponse {
  configs: ChannelConfigResponse[];
}
