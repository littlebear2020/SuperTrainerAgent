"use client";

import { UsersIcon } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatCompactNumber } from "@/core/console/format";
import { useConsoleUsers } from "@/core/console/hooks";
import { useI18n } from "@/core/i18n/hooks";

/** Sentinel for "no user filter" — Radix Select rejects an empty-string value. */
export const ALL_USERS_VALUE = "__all__";

interface Props {
  /** Whether the picker may render at all. Callers must gate on admin. */
  enabled: boolean;
  value: string;
  onChange: (value: string) => void;
}

/**
 * Admin-only user drill-down.
 *
 * Returns `null` for non-admins rather than rendering a disabled control:
 * the goal is that a regular user's console has no platform-wide affordance
 * at all, not a greyed-out one that hints the data exists. The backend
 * enforces this independently (403 on a foreign `user_id`) — this is the UI
 * half of the same rule.
 */
export function ConsoleUserPicker({ enabled, value, onChange }: Props) {
  const { t } = useI18n();
  const c = t.console;
  const { data, isLoading } = useConsoleUsers(enabled);

  if (!enabled) return null;

  const users = data?.users ?? [];

  return (
    <div className="flex items-center gap-2" data-testid="console-user-picker">
      <UsersIcon size={14} className="text-muted-foreground" />
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="h-8 w-[220px] text-xs">
          <SelectValue placeholder={c.allUsers} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL_USERS_VALUE} className="text-xs">
            {c.allUsers}
          </SelectItem>
          {isLoading && users.length === 0 ? (
            <SelectItem value="__loading__" disabled className="text-xs">
              {c.loading}
            </SelectItem>
          ) : (
            users.map((user) => (
              <SelectItem
                key={user.user_id}
                value={user.user_id}
                className="text-xs"
              >
                {user.email ?? user.user_id}
                {" · "}
                {formatCompactNumber(user.total_tokens)}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    </div>
  );
}
