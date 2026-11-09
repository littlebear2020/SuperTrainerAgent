"use client";

/**
 * 重新合成确认弹层（2026-10-06 video-gen 模块内合并批）。
 *
 * detail/storyboard（vimax）/page 三处原各持一份近似弹层。收敛为共享组件：
 * - failed 态标题切「重试合成」，否则「重新合成」
 * - failed 提示（缺素材/缺音频走全量渲染）默认显示；storyboard 版原无此句，
 *   经 showFailedHint=false 保留分叉
 * - 确认按钮统一 indigo 强调色（修前 storyboard 版无强调色）
 */
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export interface RecomposeConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isFailed: boolean;
  /** failed 态追加「缺素材请走全量渲染」提示；storyboard 版历史无此句。 */
  showFailedHint?: boolean;
  onConfirm: () => void;
}

export function RecomposeConfirmDialog({
  open,
  onOpenChange,
  isFailed,
  showFailedHint = true,
  onConfirm,
}: RecomposeConfirmDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {isFailed ? "重试合成" : "重新合成"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            复用已生成的画面素材，仅重跑视频合成（不重新生图，费用较低）。
            {isFailed &&
              showFailedHint &&
              "若重试提示缺素材/缺音频，请走「编辑分镜」全量渲染。"}
            确定继续？
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            className="bg-indigo-600 hover:bg-indigo-700"
            onClick={onConfirm}
          >
            确认合成
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
