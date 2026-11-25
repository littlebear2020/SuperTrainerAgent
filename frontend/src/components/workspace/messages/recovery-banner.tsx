import { TriangleAlertIcon, UnplugIcon } from "lucide-react";
import { useContext } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";

import { ThreadContext } from "./context";

/**
 * 会话恢复入口（PR3）。断线且 run 可能仍在服务端运行时给「重新连接」，显式停止
 * 失败时给「重试停止」——都是用户显式触发：mount 自动恢复归 SDK，这里不自动重连，
 * 也不在 join 的 Promise resolve 时宣布恢复成功（连接结果由 onFinish / onError 驱动）。
 */
export function RecoveryBanner() {
  const { t } = useI18n();
  // MessageList 也有不带 Provider 的用法（agents/new、既有测试）：没有恢复入口就不渲染
  const recovery = useContext(ThreadContext)?.recovery;
  if (!recovery) return null;

  if (recovery.cancelFailed) {
    return (
      <div
        className="mx-auto my-3 flex w-full max-w-xl items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm"
        role="alert"
        data-testid="recovery-banner"
      >
        <span className="flex items-center gap-2">
          <TriangleAlertIcon className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
          {t.recovery.cancelFailed}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={recovery.isCancelling}
          onClick={() => void recovery.retryCancel()}
        >
          {t.recovery.retryCancel}
        </Button>
      </div>
    );
  }

  if (recovery.isRecoverableDisconnect) {
    return (
      <div
        className="mx-auto my-3 flex w-full max-w-xl items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm"
        role="status"
        data-testid="recovery-banner"
      >
        <span className="flex items-center gap-2">
          <UnplugIcon className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
          {recovery.isReconnecting
            ? t.recovery.reconnecting
            : t.recovery.disconnected}
        </span>
        <Button
          size="sm"
          variant="outline"
          disabled={recovery.isReconnecting}
          onClick={() => void recovery.reconnect()}
        >
          {t.recovery.reconnect}
        </Button>
      </div>
    );
  }

  return null;
}
