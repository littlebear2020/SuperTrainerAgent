/**
 * FastAPI 错误响应共享解析（2026-10-06 近重复合并批）。
 *
 * 单一事实源：原先 memory/artifacts/video-gen/writer/voice-twin×2/gallery/
 * console/agents/skills 各持一份形状不一的 `{detail}` 读取器——同一 422 在
 * 不同模块展示格式不同。合并为以能力最全的 memory 实现（蓝本）为基准的
 * 共享解析：
 * - `detail` 为 string → 原样
 * - `detail` 为数组（FastAPI/Pydantic 校验错误 `{msg,...}[]`，也可能混 string）
 *   → 提取 msg / 序列化对象，"; " 连接
 * - `detail` 为对象/原始值 → JSON 序列化 / String()
 * - body 非 JSON 或无 detail → `${fallback} (HTTP ${status})`
 *   （writer 批次定下的回退形态：指明是服务端错误，不误导成 CSRF 问题）
 *
 * 各模块保留自己的错误类（VideoApiError/GalleryError/ConsoleApiError/
 * AgentNameCheckError 的 status 携带与分支语义不迁移，仅收敛 detail 读取）。
 * 有意不并入：auth/types.ts 的 Pydantic zod 编码映射（结构化语义，非展示串）、
 * writer/assets-api.ts 的 W19 `{message,action,reason}` 可行动错误（共享契约
 * 的超集）、uploads 的 XHR 内联解析（非 Response 形态）。
 */

/** 解析 FastAPI `{detail}` 字段为展示文案；不可解析返回 null。纯函数便于单测。 */
export function formatFastApiErrorDetail(detail: unknown): string | null {
  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }

        if (item && typeof item === "object") {
          const record = item as Record<string, unknown>;
          if (typeof record.msg === "string") {
            return record.msg;
          }

          try {
            return JSON.stringify(record);
          } catch {
            return null;
          }
        }

        return String(item);
      })
      .filter(Boolean);

    return parts.length > 0 ? parts.join("; ") : null;
  }

  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return null;
    }
  }

  if (
    typeof detail === "number" ||
    typeof detail === "boolean" ||
    typeof detail === "bigint"
  ) {
    return String(detail);
  }

  if (typeof detail === "symbol") {
    return detail.description ?? null;
  }

  return null;
}

/**
 * 读取错误响应并解析 `{detail}`。
 * body 非 JSON / 无 detail / detail 不可解析 → `${fallback} (HTTP ${status})`。
 */
export async function readFastApiErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: unknown;
  } | null;
  const message = formatFastApiErrorDetail(body?.detail);
  if (message) {
    return message;
  }
  return `${fallback} (HTTP ${response.status})`;
}
