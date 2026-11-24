import type { FileUIPart } from "ai";

export type PromptInputFilePart = FileUIPart & {
  // Transient submit-time handle to the original browser File; not serializable.
  file?: File;
};

export async function promptInputFilePartToFile(
  filePart: PromptInputFilePart,
  options?: { signal?: AbortSignal },
): Promise<File | null> {
  if (filePart.file instanceof File) {
    const filename =
      typeof filePart.filename === "string" && filePart.filename.length > 0
        ? filePart.filename
        : filePart.file.name;
    const mediaType =
      typeof filePart.mediaType === "string" && filePart.mediaType.length > 0
        ? filePart.mediaType
        : filePart.file.type;

    if (filePart.file.name === filename && filePart.file.type === mediaType) {
      return filePart.file;
    }

    return new File([filePart.file], filename, { type: mediaType });
  }

  if (!filePart.url || !filePart.filename) {
    return null;
  }
  if (options?.signal?.aborted) {
    throw Object.assign(new Error("Upload aborted"), { name: "AbortError" });
  }

  try {
    // PR2：附件 URL fallback 的 fetch 也接收取消信号（切线程 / 受理前停止）
    const response = await fetch(filePart.url, { signal: options?.signal });
    if (!response.ok) {
      throw new Error(
        `HTTP ${response.status} while fetching fallback file URL`,
      );
    }
    const blob = await response.blob();

    return new File([blob], filePart.filename, {
      type: filePart.mediaType || blob.type,
    });
  } catch (error) {
    // 取消不是「获取失败」：原样抛出（调用方按 signal 判定），不 warn、不返回 null 丢附件
    if (
      typeof error === "object" &&
      error !== null &&
      Reflect.get(error, "name") === "AbortError"
    ) {
      throw error;
    }
    console.warn("promptInputFilePartToFile: fetch fallback failed", {
      error,
      url: filePart.url,
      filename: filePart.filename,
    });
    return null;
  }
}
