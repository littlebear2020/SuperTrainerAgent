import { formatFileSize } from "@/core/utils/format";

import { MAX_FILE_SIZE_BYTES } from "./api";

const MACOS_APP_BUNDLE_CONTENT_TYPES = new Set([
  "",
  "application/octet-stream",
]);

export const MACOS_APP_BUNDLE_UPLOAD_MESSAGE =
  "macOS .app bundles can't be uploaded directly from the browser. Compress the app as a .zip or upload the .dmg instead.";

export function isLikelyMacOSAppBundle(file: Pick<File, "name" | "type">) {
  return (
    file.name.toLowerCase().endsWith(".app") &&
    MACOS_APP_BUNDLE_CONTENT_TYPES.has(file.type)
  );
}

export { formatFileSize };

export function checkFileSizeLimit(
  file: Pick<File, "name" | "size">,
  maxBytes: number = MAX_FILE_SIZE_BYTES,
): string | null {
  if (file.size > maxBytes) {
    return `${file.name} (${formatFileSize(file.size)}) exceeds the maximum upload size of ${formatFileSize(maxBytes)}.`;
  }
  return null;
}

export function splitUnsupportedUploadFiles(fileList: File[] | FileList) {
  const incoming = Array.from(fileList);
  const accepted: File[] = [];
  const rejected: File[] = [];
  const sizeErrors: string[] = [];

  for (const file of incoming) {
    if (isLikelyMacOSAppBundle(file)) {
      rejected.push(file);
      continue;
    }
    const sizeError = checkFileSizeLimit(file);
    if (sizeError) {
      sizeErrors.push(sizeError);
      continue;
    }
    accepted.push(file);
  }

  return {
    accepted,
    rejected,
    sizeErrors,
    message:
      sizeErrors.length > 0
        ? sizeErrors.join("\n")
        : rejected.length > 0
          ? MACOS_APP_BUNDLE_UPLOAD_MESSAGE
          : undefined,
  };
}
