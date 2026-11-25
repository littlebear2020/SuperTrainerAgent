import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { createContext, useContext } from "react";

import type { AgentThreadState } from "@/core/threads";

/**
 * 会话恢复入口（PR3）：由两个 chat page 从 useThreadStream 接进来；其他消费者可缺省。
 * 都是用户显式触发的动作——mount 自动恢复归 SDK，这里不自动重连。
 */
export interface ThreadRecovery {
  /** 流因连接类错误收尾、run 可能仍在服务端运行。 */
  isRecoverableDisconnect: boolean;
  isReconnecting: boolean;
  reconnect: () => Promise<void>;
  /** 最近一次显式取消失败（含超时），仍是当前 run。 */
  cancelFailed: boolean;
  /** 一次显式取消在途：「重试停止」禁用。 */
  isCancelling: boolean;
  retryCancel: () => Promise<void>;
}

export interface ThreadContextType {
  thread: BaseStream<AgentThreadState>;
  /** 自定义 run 历史的加载状态（PR3）；缺省 = 没有自定义历史的消费者。 */
  isHistoryLoading?: boolean;
  /** 仍有未加载的旧 run：为 true 时目标消息缺失不等于不存在。 */
  hasMoreHistory?: boolean;
  /** 用户显式触发的补齐式重载：带新鲜度判定，失败 reject。 */
  refetchHistory?: () => Promise<void>;
  recovery?: ThreadRecovery;
}

export const ThreadContext = createContext<ThreadContextType | undefined>(
  undefined,
);

export function useThread() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThread must be used within a ThreadContext");
  }
  return context;
}
