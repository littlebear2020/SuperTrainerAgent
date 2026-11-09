/** A3/U1: 用户已选的三视图文件（front 必填，side/back 可选）。 */
export interface PickedSheets {
  front: File;
  side?: File;
  back?: File;
}

/**
 * A3/U1: 构造角色三视图上传 FormData。
 *
 * 后端 `POST /api/video/projects/{id}/character-sheets/upload` 的 `front` 必填，
 * `side`/`back` 可选（U1：允许仅上传正面）。`features` 可选（方式 A 才用，方式 B 一般不填）。
 */
export function buildCharacterSheetUploadFormData(
  files: Partial<PickedSheets>,
  name: string,
  features = "",
): FormData {
  const fd = new FormData();
  if (files.front) fd.append("front", files.front);
  if (files.side) fd.append("side", files.side);
  if (files.back) fd.append("back", files.back);
  fd.append("name", name);
  if (features) fd.append("features", features);
  return fd;
}

/** A3: 文件 → 即时预览 object URL（调用方负责在替换/清理时 revokeObjectURL）。 */
export function previewUrl(file: File): string {
  return URL.createObjectURL(file);
}

/** A3: 清理某视图的旧预览 URL（若存在），避免内存泄漏。 */
export function revokePreview(url: string | undefined): void {
  if (url) URL.revokeObjectURL(url);
}
