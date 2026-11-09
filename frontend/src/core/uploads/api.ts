/**
 * API functions for file uploads
 */

import { buildLoginUrl } from "@/core/auth/types";

import { getBackendBaseURL } from "../config";

/** Maximum file size for uploads (2 GiB, synced with backend default). */
export const MAX_FILE_SIZE_BYTES = 2 * 1024 ** 3;

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

/**
 * Upload files with progress callback (uses XMLHttpRequest).
 *
 * Unlike `fetch`, XHR exposes `upload.onprogress` so we can report
 * real upload progress for large files. All other behavior (auth cookies,
 * CSRF headers, error handling) is identical to `uploadFiles`.
 */
export function uploadFilesWithProgress(
  threadId: string,
  files: File[],
  onProgress?: (loaded: number, total: number) => void,
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    files.forEach((file) => {
      formData.append("files", file);
    });

    const xhr = new XMLHttpRequest();
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
      if (event.lengthComputable) {
        onProgress?.(event.loaded, event.total);
      }
    });

    xhr.addEventListener("load", () => {
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

    xhr.addEventListener("error", () => {
      reject(new Error("Network error during upload"));
    });

    xhr.addEventListener("abort", () => {
      reject(new Error("Upload aborted"));
    });

    xhr.send(formData);
  });
}
