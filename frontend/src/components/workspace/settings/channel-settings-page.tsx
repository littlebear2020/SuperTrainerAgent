"use client";

import { MessageSquareIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { Switch } from "@/components/ui/switch";
import {
  useChannelConfigs,
  useDeleteChannelConfig,
  useUpdateChannelConfig,
} from "@/core/channels/hooks";
import type { ChannelConfigResponse } from "@/core/channels/types";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

const SUPPORTED_CHANNELS = [
  { type: "feishu", name: "飞书", description: "飞书/Lark 机器人集成" },
  { type: "wecom", name: "企业微信", description: "企业微信机器人集成" },
  { type: "dingtalk", name: "钉钉", description: "钉钉机器人集成" },
] as const;

export function ChannelSettingsPage() {
  const { t } = useI18n();
  const { configs, isLoading, error } = useChannelConfigs();
  const [selectedChannel, setSelectedChannel] = useState<string | null>(null);

  if (isLoading) {
    return (
      <SettingsSection
        title={t.settings.channels.title}
        description={t.settings.channels.description}
      >
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      </SettingsSection>
    );
  }

  if (error) {
    return (
      <SettingsSection
        title={t.settings.channels.title}
        description={t.settings.channels.description}
      >
        <div className="text-destructive text-sm">Error: {error.message}</div>
      </SettingsSection>
    );
  }

  // 将配置转换为Map以便快速查找
  const configMap = new Map(configs?.map((c) => [c.channel_type, c]) ?? []);

  return (
    <SettingsSection
      title={t.settings.channels.title}
      description={t.settings.channels.description}
    >
      <div className="flex w-full flex-col gap-4">
        {SUPPORTED_CHANNELS.map((channel) => {
          const config = configMap.get(channel.type);
          return (
            <ChannelItem
              key={channel.type}
              channel={channel}
              config={config}
              onConfigure={() => setSelectedChannel(channel.type)}
            />
          );
        })}
      </div>

      {selectedChannel && (
        <ChannelConfigDialog
          channelType={selectedChannel}
          channel={SUPPORTED_CHANNELS.find((c) => c.type === selectedChannel)!}
          config={configMap.get(selectedChannel)}
          onClose={() => setSelectedChannel(null)}
        />
      )}
    </SettingsSection>
  );
}

function ChannelItem({
  channel,
  config,
  onConfigure,
}: {
  channel: (typeof SUPPORTED_CHANNELS)[number];
  config?: ChannelConfigResponse;
  onConfigure: () => void;
}) {
  const { t } = useI18n();
  const { mutate: updateConfig } = useUpdateChannelConfig();
  const { mutate: deleteConfig } = useDeleteChannelConfig();

  const handleToggle = (enabled: boolean) => {
    updateConfig(
      {
        channelType: channel.type,
        config: {
          enabled,
          credentials: config?.credentials ?? {},
          config: config?.config ?? {},
        },
      },
      {
        onSuccess: () => {
          toast.success(
            enabled
              ? t.settings.channels.enabled(channel.name)
              : t.settings.channels.disabled(channel.name),
          );
        },
        onError: (error) => {
          toast.error(error.message);
        },
      },
    );
  };

  const handleDelete = () => {
    if (confirm(t.settings.channels.confirmDelete)) {
      deleteConfig(channel.type, {
        onSuccess: () => {
          toast.success(t.settings.channels.deleted(channel.name));
        },
        onError: (error) => {
          toast.error(error.message);
        },
      });
    }
  };

  return (
    <Item className="w-full" variant="outline">
      <ItemContent>
        <ItemTitle>
          <div className="flex items-center gap-2">
            <MessageSquareIcon className="size-4" />
            <div>{channel.name}</div>
          </div>
        </ItemTitle>
        <ItemDescription>{channel.description}</ItemDescription>
      </ItemContent>
      <ItemActions>
        {config && (
          <>
            <Button variant="ghost" size="icon" onClick={handleDelete}>
              <Trash2Icon className="size-4" />
            </Button>
            <Switch checked={!!config.enabled} onCheckedChange={handleToggle} />
          </>
        )}
        {!config && (
          <Button variant="outline" size="sm" onClick={onConfigure}>
            <PlusIcon className="size-4" />
            {t.settings.channels.configure}
          </Button>
        )}
      </ItemActions>
    </Item>
  );
}

function ChannelConfigDialog({
  channelType,
  channel,
  config,
  onClose,
}: {
  channelType: string;
  channel: (typeof SUPPORTED_CHANNELS)[number];
  config?: ChannelConfigResponse;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const { mutate: updateConfig, isPending } = useUpdateChannelConfig();
  const [credentials, setCredentials] = useState<Record<string, string>>(
    config?.credentials ?? {},
  );

  const handleSave = () => {
    updateConfig(
      {
        channelType,
        config: {
          enabled: true,
          credentials,
          config: config?.config ?? {},
        },
      },
      {
        onSuccess: () => {
          toast.success(t.settings.channels.saved);
          onClose();
        },
        onError: (error) => {
          toast.error(error.message);
        },
      },
    );
  };

  const getFieldsForChannel = () => {
    switch (channelType) {
      case "feishu":
        return [
          {
            key: "app_id",
            label: "App ID",
            placeholder: "cli_xxxxxxxxxxxx",
          },
          {
            key: "app_secret",
            label: "App Secret",
            placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            type: "password",
          },
        ];
      case "wecom":
        return [
          {
            key: "bot_id",
            label: "Bot ID",
            placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
          },
          {
            key: "bot_secret",
            label: "Bot Secret",
            placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            type: "password",
          },
        ];
      case "dingtalk":
        return [
          { key: "client_id", label: "Client ID", placeholder: "ding..." },
          {
            key: "client_secret",
            label: "Client Secret",
            placeholder: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            type: "password",
          },
        ];
      default:
        return [];
    }
  };

  const fields = getFieldsForChannel();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-background w-full max-w-md rounded-lg border p-6 shadow-lg">
        <h2 className="text-lg font-semibold">
          {t.settings.channels.configureChannel(channel.name)}
        </h2>
        <p className="text-muted-foreground mt-1 text-sm">
          {channel.description}
        </p>

        <div className="mt-6 space-y-4">
          {fields.map((field) => (
            <div key={field.key} className="space-y-2">
              <label className="text-sm font-medium">{field.label}</label>
              <input
                type={field.type ?? "text"}
                placeholder={field.placeholder}
                value={credentials[field.key] ?? ""}
                onChange={(e) =>
                  setCredentials({
                    ...credentials,
                    [field.key]: e.target.value,
                  })
                }
                className="border-input bg-background ring-offset-background placeholder:text-muted-foreground focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm file:border-0 file:bg-transparent file:text-sm file:font-medium focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
          ))}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={isPending}>
            {t.common.cancel}
          </Button>
          <Button onClick={handleSave} disabled={isPending}>
            {isPending ? "保存中..." : t.common.save}
          </Button>
        </div>
      </div>
    </div>
  );
}
