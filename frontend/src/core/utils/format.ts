/**
 * Shared money formatting (2026-08-13, 批次 C Phase D).
 *
 * Moved out of ``core/console/format.ts`` so task views (video-gen list /
 * detail, voice production history, writer export) can render per-task cost
 * with the same semantics as the console: ``null`` means "cannot price"
 * and renders as "—" rather than "0.00" — a zero would claim the task was
 * free when the truth is that we do not know what it cost. The console
 * module re-exports these for backward compatibility.
 */

function currencySymbol(currency: string | null | undefined): string {
  switch ((currency ?? "").toUpperCase()) {
    case "CNY":
      return "¥";
    case "USD":
      return "$";
    case "EUR":
      return "€";
    case "":
      return "";
    default:
      return `${currency} `;
  }
}

/**
 * Money, or an explicit "unpriced" marker.
 *
 * `null` means no pricing is configured for the models involved. That is
 * shown as "—" rather than "0.00", because a zero would claim the run was
 * free when the truth is that we do not know what it cost.
 */
export function formatCost(
  value: number | null | undefined,
  currency: string | null | undefined,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const symbol = currencySymbol(currency);
  // Sub-cent spend still matters when it is per-run and there are thousands
  // of runs, so show 4 decimals below 1 unit instead of rounding to 0.00.
  const digits = Math.abs(value) > 0 && Math.abs(value) < 1 ? 4 : 2;
  return `${symbol}${value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export { currencySymbol };

/**
 * 文件大小展示（KB/MB/GB，1 位小数；GB 2 位）。2026-10-06 合并批：
 * 单一事实源——修前 file-validation（导出）与 message-list-item /
 * production-history 本地副本三处。
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}
