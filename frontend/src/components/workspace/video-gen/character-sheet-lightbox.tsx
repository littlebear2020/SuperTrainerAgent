"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useState } from "react";

export type SheetView = "front" | "side" | "back";

export const SHEET_LABEL: Record<SheetView, string> = {
  front: "正面",
  side: "侧面",
  back: "背面",
};

/**
 * Bug 1 fix: 返回 serve-by-index 的 HTTP URL（FS 路径不能作 <img loading="lazy" src>）。
 *
 * 修前 `resolveSheetImgUrl(sheet, view, projectId)` 返回 `sheet.front_url`（服务器 FS 路径），
 * 浏览器解析为相对 URL → 404；且 front_url 永非空 → fallback serving 路由永不触发。
 */
export function resolveSheetImgUrl(
  view: SheetView,
  projectId: string,
  sheetId: string,
): string {
  // M1: serve 按稳定 id（下标随删除/排序漂移会串图）
  return `/api/video/projects/${projectId}/character-sheets/${encodeURIComponent(sheetId)}/${view}`;
}

/**
 * X11-2（2026-08-27 计划）：三视图合成图缩略图 serve URL——同构 by-id serve 端点
 * （tag=composite；后端白名单已扩）。独立函数不动 SheetView 联合类型（lightbox
 * 视图切换仍 front/side/back 三值）。
 */
export function resolveCompositeImgUrl(
  projectId: string,
  sheetId: string,
): string {
  return `/api/video/projects/${projectId}/character-sheets/${encodeURIComponent(sheetId)}/composite`;
}

/**
 * A1: 三视图点击放大预览。radix Dialog 全屏遮罩 + 原图 `object-contain`。
 * U1/F3: 缺视图（serve 路由 404）时 onError 显「该视图无图像」，切视图重置失败态。
 */
export function CharacterSheetLightbox({
  open,
  onOpenChange,
  projectId,
  sheetId,
  initialView,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projectId: string;
  sheetId: string;
  initialView: SheetView;
}) {
  const src = resolveSheetImgUrl(initialView, projectId, sheetId);
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/80" />
        <Dialog.Content className="fixed top-1/2 left-1/2 z-50 -translate-x-1/2 -translate-y-1/2">
          <Dialog.Title className="sr-only">
            {SHEET_LABEL[initialView]}预览
          </Dialog.Title>
          {failed ? (
            <div className="rounded-lg bg-white p-8 text-sm text-neutral-600">
              该视图无图像
            </div>
          ) : (
            <img
              loading="lazy"
              src={src}
              alt={SHEET_LABEL[initialView]}
              onError={() => setFailed(true)}
              className="max-h-[85vh] w-auto max-w-[90vw] rounded-lg object-contain"
            />
          )}
          <Dialog.Close className="absolute top-2 right-2 rounded-full bg-black/40 p-1 text-white">
            <X className="h-4 w-4" />
            <span className="sr-only">关闭</span>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
