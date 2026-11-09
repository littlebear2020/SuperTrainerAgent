"use client";

import {
  ChevronLeftIcon,
  ChevronRightIcon,
  LoaderIcon,
  Table2Icon,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useDelimitedPreview } from "@/core/artifacts/use-delimited-preview";
import { copyToClipboard } from "@/lib/utils";

// （PR-3 5.2：自上游 DeerFlow artifact-table-preview.tsx 移植；复制改走本地
// lib/utils copyToClipboard（上游 core/clipboard 不引入）。i18n 键集
// （上游 t.artifactTable.*）不在本 PR allow 路径内——暂以本地中文常量承载，
// i18n 化留规划侧后续批次（范围外发现已登记收尾打包）。）
const PAGE_SIZE = 50;
const MAX_DATA_ROWS = 200;

const LABELS = {
  title: "表格预览",
  header: "首行为表头",
  column: (index: number) => `第 ${index} 列`,
  missing: "缺失",
  cellValue: "单元格内容",
  cell: (row: number, column: number) => `第 ${row} 行第 ${column} 列`,
  failed: "无法可靠预览此表格文件",
  retry: "重试",
  empty: "文件为空",
  incomplete: "文件不完整，无可展示的完整行",
  sample: (rows: number) => `抽样预览：前 ${rows} 行`,
  total: (rows: number) => `共 ${rows} 行`,
  columnsLimited: "列数超过 50，已截断显示。",
  uneven: "行列不齐。",
  previous: "上一页",
  next: "下一页",
} as const;

interface TablePreviewProps {
  content: string;
  delimiter: "," | "\t";
  truncated: boolean;
  identity: string;
  active?: boolean;
}

export function ArtifactTablePreview(props: TablePreviewProps) {
  return <TablePreview key={props.identity} {...props} />;
}

function TablePreview({
  content,
  delimiter,
  truncated,
  identity,
  active = true,
}: TablePreviewProps) {
  const { result, status, retry } = useDelimitedPreview({
    content,
    delimiter,
    truncated,
    identity,
    active,
  });
  const [hasHeader, setHasHeader] = useState(true);
  const [pagination, setPagination] = useState({ content, page: 0 });
  const [cell, setCell] = useState<{
    value: string;
    label: string;
    content: string;
  } | null>(null);
  const [copyStatus, setCopyStatus] = useState("");
  const page = pagination.content === content ? pagination.page : 0;
  const rows =
    result?.rows.slice(
      hasHeader ? 1 : 0,
      (hasHeader ? 1 : 0) + MAX_DATA_ROWS,
    ) ?? [];
  const limited = Boolean(
    result?.limited === true ||
    (result && result.rows.length > MAX_DATA_ROWS + Number(hasHeader)),
  );
  const columnCount = Math.min(result?.columnCount ?? 0, 50);
  const start = Math.min(page * PAGE_SIZE, Math.max(0, rows.length - 1));
  const end = Math.min(start + PAGE_SIZE, rows.length);
  const columns = Array.from({ length: columnCount }, (_, index) => index);

  return (
    <div
      hidden={!active}
      className="flex h-full min-h-0 flex-col"
      data-testid="artifact-table-preview"
    >
      {status === "error" ? (
        <div
          role="status"
          className="text-muted-foreground flex flex-col items-center gap-3 p-8 text-center text-sm"
        >
          <p>{LABELS.failed}</p>
          <Button variant="outline" onClick={retry}>
            {LABELS.retry}
          </Button>
        </div>
      ) : status === "loading" || !result ? (
        <div
          role="status"
          className="text-muted-foreground flex items-center gap-2 p-6 text-sm"
        >
          <LoaderIcon className="size-4 animate-spin" />
          加载中...
        </div>
      ) : result.rows.length === 0 ? (
        <p role="status" className="text-muted-foreground p-6 text-sm">
          {limited ? LABELS.incomplete : LABELS.empty}
        </p>
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b px-4 py-3 text-xs">
            <span className="flex items-center gap-2 font-medium">
              <Table2Icon className="text-muted-foreground size-4" />
              {limited ? LABELS.sample(rows.length) : LABELS.total(rows.length)}
            </span>
            <label className="text-muted-foreground flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={hasHeader}
                onChange={(event) => {
                  setHasHeader(event.target.checked);
                  setPagination({ content, page: 0 });
                }}
                className="accent-primary size-3.5"
              />
              {LABELS.header}
            </label>
          </div>
          {(result.columnCount > 50 || result.unevenRows) && (
            <p
              role="status"
              className="bg-muted/30 text-muted-foreground border-b px-4 py-2 text-xs"
            >
              {result.columnCount > 50 && LABELS.columnsLimited}{" "}
              {result.unevenRows && LABELS.uneven}
            </p>
          )}
          <div
            className="min-h-0 flex-1 overflow-auto"
            tabIndex={0}
            role="region"
            aria-label={LABELS.title}
          >
            <table
              aria-label={LABELS.title}
              className="w-full table-fixed border-separate border-spacing-0 text-sm"
              style={{ minWidth: Math.max(320, columnCount * 180 + 48) }}
            >
              <colgroup>
                <col style={{ width: 48 }} />
                {columns.map((index) => (
                  <col key={index} />
                ))}
              </colgroup>
              <thead className="bg-muted sticky top-0 z-10">
                <tr>
                  <th
                    scope="col"
                    className="text-muted-foreground border-b px-3 py-3 text-xs font-normal"
                  >
                    #
                  </th>
                  {columns.map((index) => (
                    <th
                      key={index}
                      scope="col"
                      className="truncate border-b border-l px-3 py-3 text-left text-xs font-medium"
                      title={hasHeader ? result.rows[0]?.[index] : undefined}
                    >
                      {hasHeader
                        ? (result.rows[0]?.[index] ?? "")
                        : LABELS.column(index + 1)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(start, end).map((row, rowIndex) => (
                  <tr
                    key={start + rowIndex}
                    className="even:bg-muted/20 hover:bg-muted/40"
                  >
                    <th
                      scope="row"
                      className="text-muted-foreground border-b px-3 py-2.5 text-right text-xs font-normal tabular-nums"
                    >
                      {start + rowIndex + 1}
                    </th>
                    {columns.map((column) => {
                      const value = row[column];
                      const expandable =
                        value !== undefined &&
                        (value.length > 120 || /[\r\n]/.test(value));
                      return (
                        <td
                          key={column}
                          className="border-b border-l px-3 py-2.5 align-top"
                        >
                          <div className="truncate">
                            {value === undefined ? (
                              <span className="text-muted-foreground text-xs italic">
                                {LABELS.missing}
                              </span>
                            ) : expandable ? (
                              <button
                                type="button"
                                className="max-w-full cursor-pointer truncate text-left underline underline-offset-4"
                                aria-label={LABELS.cell(
                                  start + rowIndex + 1,
                                  column + 1,
                                )}
                                onClick={() => {
                                  setCell({
                                    content,
                                    value,
                                    label: LABELS.cell(
                                      start + rowIndex + 1,
                                      column + 1,
                                    ),
                                  });
                                  setCopyStatus("");
                                }}
                              >
                                {value.slice(0, 120)}
                              </button>
                            ) : (
                              <span className="whitespace-pre" title={value}>
                                {value}
                              </span>
                            )}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-muted-foreground flex shrink-0 items-center justify-between border-t px-4 py-2 text-xs">
            <span aria-live="polite">
              第 {rows.length ? start + 1 : 0}-{end} 行
              {limited ? "（抽样）" : `，共 ${rows.length} 行`}
            </span>
            <div className="flex gap-1">
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={LABELS.previous}
                disabled={page === 0}
                onClick={() => setPagination({ content, page: page - 1 })}
              >
                <ChevronLeftIcon className="size-4" />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={LABELS.next}
                disabled={end >= rows.length}
                onClick={() => setPagination({ content, page: page + 1 })}
              >
                <ChevronRightIcon className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
      <Dialog
        open={cell !== null && cell.content === content && active}
        onOpenChange={(open) => {
          if (!open) setCell(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{LABELS.cellValue}</DialogTitle>
            <DialogDescription>{cell?.label}</DialogDescription>
          </DialogHeader>
          <textarea
            aria-label={LABELS.cellValue}
            readOnly
            value={cell?.value ?? ""}
            className="h-64 w-full resize-none rounded-md border p-3 font-mono text-sm"
          />
          <div className="flex items-center justify-end gap-3">
            <span role="status" className="text-muted-foreground text-xs">
              {copyStatus}
            </span>
            <Button
              variant="outline"
              onClick={() => {
                void copyToClipboard(cell?.value ?? "")
                  .then(() => setCopyStatus("已复制到剪贴板"))
                  .catch(() => setCopyStatus("复制失败"));
              }}
            >
              复制到剪贴板
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
