import { CheckIcon, CopyIcon } from "lucide-react";
import { useCallback, useState, type ComponentProps } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { copyToClipboard } from "@/lib/utils";

import { Tooltip } from "./tooltip";

export function CopyButton({
  clipboardData,
  ...props
}: ComponentProps<typeof Button> & {
  clipboardData: string;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    void copyToClipboard(clipboardData).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [clipboardData]);
  return (
    <Tooltip content={t.clipboard.copyToClipboard}>
      <Button
        size="icon-sm"
        type="button"
        variant="ghost"
        className="text-muted-foreground hover:bg-indigo-500/10 hover:text-indigo-600 dark:hover:text-indigo-400"
        onClick={handleCopy}
        {...props}
      >
        {copied ? (
          <CheckIcon className="text-emerald-500" size={12} />
        ) : (
          <CopyIcon size={12} />
        )}
      </Button>
    </Tooltip>
  );
}
