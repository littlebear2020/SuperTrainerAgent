"use client";

import { CheckCircle2, Copy, Link2, Loader2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatDateTime } from "@/core/utils/datetime";
import {
  classifyShareLoadResult,
  createDocShare,
  getDocShare,
  revokeDocShare,
} from "@/core/writer/api";

interface ShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  docId: string;
  docTitle: string;
}

const EXPIRY_OPTIONS = [
  { value: "7", label: "7 天" },
  { value: "30", label: "30 天" },
  { value: "0", label: "永久" },
] as const;

export function ShareDialog({
  open,
  onOpenChange,
  docId,
  docTitle,
}: ShareDialogProps) {
  const [shareData, setShareData] = useState<{
    share_token: string;
    expires_at: string | null;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [copied, setCopied] = useState(false);
  const [expiresDays, setExpiresDays] = useState<string>("0");
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Load existing share on open — distinguish "no share" from "load failed"
  const loadShare = useCallback(() => {
    setLoading(true);
    setError(null);
    setLoadError(null);
    getDocShare(docId)
      .then((data) => {
        const result = classifyShareLoadResult(data);
        if (result.state === "loaded") {
          setShareData({
            share_token: result.data.share_token,
            expires_at: result.data.expires_at,
          });
        } else {
          setShareData(null);
        }
      })
      .catch((err: unknown) => {
        const result = classifyShareLoadResult(err);
        if (result.state === "failed") {
          setLoadError(result.error);
        } else {
          setLoadError("加载分享状态失败");
        }
      })
      .finally(() => setLoading(false));
  }, [docId]);

  useEffect(() => {
    if (!open) return;
    loadShare();
  }, [open, loadShare]);

  const handleCreate = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await createDocShare(docId, {
        expires_days: expiresDays === "0" ? undefined : Number(expiresDays),
      });
      setShareData({
        share_token: data.share_token,
        expires_at: data.expires_at,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建分享失败");
    } finally {
      setLoading(false);
    }
  }, [docId, expiresDays]);

  const handleRevoke = useCallback(async () => {
    setRevoking(true);
    try {
      await revokeDocShare(docId);
      setShareData(null);
    } catch {
      setError("撤销失败");
    } finally {
      setRevoking(false);
    }
  }, [docId]);

  const shareUrl = shareData
    ? `${window.location.origin}/share/writer/${shareData.share_token}`
    : "";

  const handleCopy = useCallback(() => {
    if (!shareUrl) return;
    void navigator.clipboard.writeText(shareUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [shareUrl]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-indigo-500" />
            分享文档
          </DialogTitle>
          <DialogDescription>
            生成分享链接，允许他人查看「{docTitle}」
          </DialogDescription>
        </DialogHeader>

        {error && <p className="text-xs text-red-500">{error}</p>}

        {loadError ? (
          <div className="space-y-3 py-4">
            <p className="text-sm text-red-500">{loadError}</p>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
              <Button onClick={loadShare} disabled={loading}>
                {loading && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                重试
              </Button>
            </DialogFooter>
          </div>
        ) : shareData ? (
          <div className="space-y-3 py-4">
            <div className="flex items-center gap-2">
              <input
                readOnly
                value={shareUrl}
                className="flex-1 rounded-md border border-[var(--border)] bg-[var(--muted)] px-3 py-2 text-sm"
                onClick={(e) => (e.target as HTMLInputElement).select()}
              />
              <Button size="sm" variant="outline" onClick={handleCopy}>
                {copied ? (
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </Button>
            </div>
            <p className="text-xs text-[var(--muted-foreground)]">
              分享内容随你的编辑实时更新，只读
            </p>
            {/* 2026-09-14 产品拍板（决策#2）：公开语义明示，确保用户了解分享范围 */}
            <p className="text-xs text-amber-600 dark:text-amber-400">
              链接公开有效，任何获得链接的人无需登录即可查看
            </p>
            {shareData.expires_at && (
              <p className="text-xs text-[var(--muted-foreground)]">
                有效期至 {formatDateTime(shareData.expires_at)}
              </p>
            )}
            <DialogFooter>
              <Button
                variant="destructive"
                size="sm"
                onClick={handleRevoke}
                disabled={revoking}
              >
                {revoking && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                <XCircle className="mr-1 h-3 w-3" />
                撤销分享
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-3 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">有效期</label>
              <Select value={expiresDays} onValueChange={setExpiresDays}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EXPIRY_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                取消
              </Button>
              <Button onClick={handleCreate} disabled={loading}>
                {loading && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                生成分享链接
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
