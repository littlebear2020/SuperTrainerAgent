/**
 * API functions for file uploads
 */

import { buildLoginUrl } from "@/core/auth/types";

import { getBackendBaseURL } from "../config";

/** Maximum file size for uploads (2 GiB, synced with backend default). */
export const MAX_FILE_SIZE_BYTES = 2 * 1024 ** 3;

/** 网络无进展的上限（毫秒）：上传与等待响应共用同一个空闲计时。 */
export const UPLOAD_IDLE_TIMEOUT_MS = 120_000;

export interface UploadedFileInfo {
  filename: string;
  size: number;
  path: string;
  virtual_path: string;
  artifact_url: string;
  extension?: string;
  modified?: number;
  markdown_file?: string;
  markdown_path?: string;
  markdown_virtual_path?: string;
  markdown_artifact_url?: string;
}

export interface UploadResponse {
  success: boolean;
  files: UploadedFileInfo[];
  message: string;
}

export interface UploadOptions {
  /** 外部取消（切线程 / 卸载 / 受理前停止）：已 aborted 时不发 XHR，中途 abort 则中止 XHR。 */
  signal?: AbortSignal;
  /** 连续无网络进展多久判失败，默认 120 秒；每次网络进展重置。 */
  idleTimeoutMs?: number;
}

function uploadAbortedError(): Error {
  const error = new Error("Upload aborted");
  error.name = "AbortError";
  return error;
}

/**
 * Upload files with progress callback (uses XMLHttpRequest).
 *
 * Unlike `fetch`, XHR exposes `upload.onprogress` so we can report
 * real upload progress for large files. All other behavior (auth cookies,
 * CSRF headers, error handling) is identical to `uploadFiles`.
 *
 * 空闲超时与取消（PR2，2026-11-24 前端可靠性批）：此前没有任何超时，网络半开时
 * 上传会永远挂着、输入框一直处于上传态。现在上传与等待响应共用一个空闲计时，
 * 每次网络进展（上行或下行的 progress 事件）重置；超时、load、error、abort、
 * 外部取消都清理监听与计时器，Promise 只 settle 一次。
 */
export function uploadFilesWithProgress(
  threadId: string,
  files: File[],
  onProgress?: (loaded: number, total: number) => void,
  options?: UploadOptions,
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const signal = options?.signal;
    if (signal?.aborted) {
      reject(uploadAbortedError());
      return;
    }

    const formData = new FormData();
    files.forEach((file) => {
      formData.append("files", file);
    });

    const xhr = new XMLHttpRequest();
    const idleTimeoutMs = options?.idleTimeoutMs ?? UPLOAD_IDLE_TIMEOUT_MS;
    let settled = false;
    let idleTimer: number | undefined;

    const clearIdleTimer = () => {
      if (idleTimer !== undefined) {
        window.clearTimeout(idleTimer);
        idleTimer = undefined;
      }
    };
    const settle = (action: () => void) => {
      if (settled) return;
      settled = true;
      clearIdleTimer();
      signal?.removeEventListener("abort", onExternalAbort);
      action();
    };
    // 每次网络进展（上行 progress 或下行 progress）重置空闲计时
    const touch = () => {
      clearIdleTimer();
      idleTimer = window.setTimeout(() => {
        settle(() => {
          xhr.abort();
          reject(
            new Error(
              `Upload stalled: no network progress for ${Math.round(idleTimeoutMs / 1000)}s`,
            ),
          );
        });
      }, idleTimeoutMs);
    };
    const onExternalAbort = () => {
      settle(() => {
        xhr.abort();
        reject(uploadAbortedError());
      });
    };
    signal?.addEventListener("abort", onExternalAbort);

    xhr.open("POST", `${getBackendBaseURL()}/api/threads/${threadId}/uploads`);
    xhr.withCredentials = true;

    // Extract CSRF token from cookies so auth middleware accepts the request
    const csrfToken =
      typeof document !== "undefined"
        ? (document.cookie
            .split("; ")
            .find((row) => row.startsWith("csrf_token="))
            ?.split("=")[1] ?? "")
        : "";
    if (csrfToken) {
      xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    }

    xhr.upload.addEventListener("progress", (event) => {
      touch();
      if (event.lengthComputable) {
        onProgress?.(event.loaded, event.total);
      }
    });
    // 上传完、等响应期间：下行进展同样算网络进展
    xhr.addEventListener("progress", () => touch());

    xhr.addEventListener("load", () => {
      settle(() => {
        if (xhr.status === 401) {
          window.location.href = buildLoginUrl(window.location.pathname);
          reject(new Error("Unauthorized"));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as UploadResponse);
          } catch {
            reject(new Error(`${xhr.status}: Invalid JSON response`));
          }
        } else {
          let detail = xhr.statusText;
          try {
            const error = JSON.parse(xhr.responseText);
            if (typeof error.detail === "string") {
              detail = error.detail;
            }
          } catch {
            // use status text
          }
          reject(new Error(`${xhr.status}: ${detail}`));
        }
      });
    });

    xhr.addEventListener("error", () => {
      settle(() => reject(new Error("Network error during upload")));
    });

    xhr.addEventListener("abort", () => {
      settle(() => reject(uploadAbortedError()));
    });

    touch();
    xhr.send(formData);
  });
}
