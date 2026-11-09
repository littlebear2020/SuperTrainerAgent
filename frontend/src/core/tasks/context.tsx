import {
  type Dispatch,
  createContext,
  useCallback,
  useContext,
  useState,
  type SetStateAction,
} from "react";

import { mergeSteps } from "./steps";
import type { Subtask } from "./types";

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
  setTasks: Dispatch<SetStateAction<Record<string, Subtask>>>;
}

export const SubtaskContext = createContext<SubtaskContextValue>({
  tasks: {},
  setTasks: () => {
    /* noop */
  },
});

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  return (
    <SubtaskContext.Provider value={{ tasks, setTasks }}>
      {children}
    </SubtaskContext.Provider>
  );
}

export function useSubtaskContext() {
  const context = useContext(SubtaskContext);
  if (context === undefined) {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  }
  return context;
}

export function useSubtask(id: string) {
  const { tasks } = useSubtaskContext();
  return tasks[id];
}

/**
 * Pure merge of an existing subtask with an incoming patch.
 *
 * Extracted from useUpdateSubtask so it is unit-testable without a DOM
 * (@testing-library/react is not installed — see tests/unit/core/writer/
 * hooks.test.ts for the same convention). Guards against regressing a terminal
 * status back to in_progress: a late full-metadata re-derivation
 * (useSyncSubtaskStatus before the tool-result message streams in) must not
 * clobber a status that streaming (onCustomEvent) already settled.
 *
 * Returns the EXISTING object reference when the patch changes nothing. This is
 * load-bearing, not an optimization: useSyncSubtaskStatus re-derives an
 * equal-but-new patch on every render (thread.messages is a fresh array each
 * render), so an unconditional spread stored a new object every time → context
 * value changed → re-render → re-derive, forever. React only bails out of a
 * setState when the next state is Object.is-identical to the previous, so
 * identity here is what breaks the cycle (React error #185, chat white screen).
 *
 * An explicit-undefined patch field is treated as "absent" rather than a
 * clobber — latestMessage arrives only from streaming events and must survive
 * re-derivation (same intent as the deriveSubtasks omission, now enforced here).
 */
export function applySubtaskPatch(
  existing: Subtask | undefined,
  patch: Partial<Subtask> & { id: string },
): Subtask {
  const effective = Object.fromEntries(
    Object.entries(patch).filter(([, value]) => value !== undefined),
  ) as Partial<Subtask> & { id: string };

  if (
    existing &&
    existing.status !== "in_progress" &&
    effective.status === "in_progress"
  ) {
    // Terminal status already settled by streaming — drop the regression.
    delete effective.status;
  }

  // PR-3 5.1：steps 特判走 mergeSteps（按 message_index 去重、incoming 胜出、
  // 有序输出）——每条 task_running 事件只带一条 delta，禁 spread（spread 会把
  // 存量步骤整组覆盖，丢失先前 delta）。合并结果与旧数组逐位同引用时沿用旧
  // 引用（保持 #185 的 Object.is bail-out 语义）。注意：同 message_index 的
  // 重复事件 incoming 为新对象引用，仍会覆盖并触发一次重渲染——SSE 事件源
  // 有限条，无死循环面；bail-out 兜的是「无 steps 字段的重复 patch」等路径。
  if (existing && effective.steps && effective.steps.length > 0) {
    const previous = existing.steps ?? [];
    const merged = mergeSteps(previous, effective.steps);
    const unchanged =
      merged.length === previous.length &&
      merged.every((step, index) => step === previous[index]);
    effective.steps = unchanged ? previous : merged;
  }

  if (
    existing &&
    (Object.keys(effective) as (keyof Subtask)[]).every((key) =>
      Object.is(existing[key], effective[key]),
    )
  ) {
    return existing;
  }

  return { ...(existing ?? {}), ...effective } as Subtask;
}

export function useUpdateSubtask() {
  const { setTasks } = useSubtaskContext();
  const updateSubtask = useCallback(
    (task: Partial<Subtask> & { id: string }) => {
      // Functional update: avoids the stale-closure mutation the old impl had
      // (it read `tasks` from the closure and only setTasks on latestMessage,
      // so terminal statuses from onCustomEvent were never persisted).
      setTasks((prev) => {
        const merged = applySubtaskPatch(prev[task.id], task);
        // Return the previous map identity when nothing changed so React bails
        // out of the update. Without this the context value is new on every
        // re-derivation and the render loop never settles (React #185).
        if (prev[task.id] === merged) {
          return prev;
        }
        return { ...prev, [task.id]: merged };
      });
    },
    [setTasks],
  );
  return updateSubtask;
}
