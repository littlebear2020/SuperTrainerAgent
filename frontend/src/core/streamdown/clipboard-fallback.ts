/**
 * Patch navigator.clipboard with a fallback when the API is unavailable
 * (non-HTTPS/HTTP deployments) so Streamdown's built-in table/code copy and
 * download controls keep working.
 *
 * （PR-2 3.4：上游对应件是 core/clipboard 的 installClipboardFallback；本地
 * 依赖裁剪拍板——不引入 core/clipboard 模块。⚠️ 评审 critical 订正：不能委托
 * lib/utils 的 copyToClipboard——其首行 `if (navigator.clipboard)` 在补丁装上
 * 后恒真，两条路径互指成无限递归（http 部署下所有复制必败）。这里内联
 * textarea/execCommand 路径，与 copyToClipboard 的兜底分支同源但不重入
 * navigator.clipboard 检查——同上游 fallbackWriteText 的防递归形态。）
 */
function fallbackWriteText(text: string): Promise<void> {
  return new Promise((resolve, reject) => {
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      document.body.removeChild(textarea);
      if (copied) {
        resolve();
      } else {
        reject(new Error("execCommand copy failed"));
      }
    } catch (error) {
      reject(error instanceof Error ? error : new Error(String(error)));
    }
  });
}

export function installClipboardFallback(): void {
  if (typeof navigator === "undefined" || navigator.clipboard) {
    return;
  }
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: (text: string) => fallbackWriteText(text),
    },
  });
}
