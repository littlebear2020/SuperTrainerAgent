/**
 * A3: pricing panel component tests.
 *
 * Uses the same createRoot + act + happy-dom harness as
 * console-admin-gating.test.tsx (no @testing-library/react in this repo).
 *
 * Covers: ① gate (enabled=false → null + fetch not called)
 *         ② admin sees rows = union of models + pricing keys
 *         ③ save submits ALL models (full-table PUT)
 *         ④ cache_hit empty → null / "0" → 0
 *         ⑤ 422 → toast.error
 *         ⑥ source="yaml" rows show a source badge
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsolePricingPanel } from "@/components/workspace/console/console-pricing-panel";
import { DialogProvider } from "@/components/workspace/dialog-provider";
import { I18nProvider } from "@/core/i18n/context";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockFetchModelPricing = vi.fn();
const mockPutModelPricing = vi.fn();
const mockFetchMediaPricing = vi.fn();
const mockPutMediaPricing = vi.fn();
const mockPatchMediaPricing = vi.fn();
const mockDeleteMediaPricing = vi.fn();
const mockUseModels = vi.fn();

vi.mock("@/core/console/api", () => ({
  ConsoleApiError: class ConsoleApiError extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
      this.name = "ConsoleApiError";
    }
  },
  fetchModelPricing: (...args: unknown[]) => mockFetchModelPricing(...args),
  putModelPricing: (...args: unknown[]) => mockPutModelPricing(...args),
  fetchMediaPricing: (...args: unknown[]) => mockFetchMediaPricing(...args),
  putMediaPricing: (...args: unknown[]) => mockPutMediaPricing(...args),
  patchMediaPricing: (...args: unknown[]) => mockPatchMediaPricing(...args),
  deleteMediaPricing: (...args: unknown[]) => mockDeleteMediaPricing(...args),
}));

vi.mock("@/core/models/hooks", () => ({
  useModels: (...args: unknown[]) => mockUseModels(...args),
}));

vi.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ isAdmin: true, user: { id: "admin-1" } }),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function Wrap({
  children,
  queryClient,
}: {
  children: ReactNode;
  queryClient: QueryClient;
}) {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider initialLocale="zh-CN">
        {/* OBS-3（2026-10-09）：确认走 useConfirm 弹层——测试须挂真 Provider */}
        <DialogProvider>{children}</DialogProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}

/** 弹层（ConfirmDialog）按钮：文案硬编码中文（取消/确定），与面板 locale 无关。 */
function dialogButton(text: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll("button")).find(
    (b) => b.textContent?.trim() === text,
  );
}

async function clickDialog(text: string) {
  const btn = dialogButton(text);
  expect(btn, `弹层应出现「${text}」按钮`).toBeTruthy();
  await act(async () => {
    btn!.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function dialogVisible(): boolean {
  return Boolean(dialogButton("确定") && dialogButton("取消"));
}

const PRICING_DATA = {
  pricing: {
    "model-a": {
      currency: "CNY",
      input_per_million: 8,
      output_per_million: 32,
      input_cache_hit_per_million: 0.8,
      updated_at: "2026-08-08T00:00:00Z",
      updated_by: "admin-1",
      source: "db" as const,
    },
    "model-b": {
      currency: "CNY",
      input_per_million: 5,
      output_per_million: 20,
      input_cache_hit_per_million: null,
      updated_at: null,
      updated_by: "",
      source: "yaml" as const,
    },
  },
};

const MODELS = {
  models: [
    { name: "model-a", display_name: "Model A" },
    { name: "model-b", display_name: "Model B" },
    { name: "model-c", display_name: "Model C" },
  ],
  tokenUsageEnabled: false,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("A3 console-pricing-panel", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    // review-②（2026-09-14）：无 cookie 新访客默认 zh-CN（DSN-003b 拍板 #6）后，
    // 渲染语言不再由 happy-dom 的 navigator 兜底成 en-US——显式钉 cookie 保持 en 断言。
    document.cookie = "locale=en-US; path=/";
    vi.clearAllMocks();
    mockFetchModelPricing.mockResolvedValue(PRICING_DATA);
    mockUseModels.mockReturnValue(MODELS);
    mockPutModelPricing.mockResolvedValue(PRICING_DATA);
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [
        {
          provider: "seedream",
          media_type: "image",
          unit: "per_image",
          unit_price: 0.22,
          currency: "CNY",
          updated_at: null,
          updated_by: "",
          source: "default",
        },
        {
          provider: "seedance-ark",
          media_type: "video",
          unit: "per_kilo_token",
          unit_price: 0.023,
          currency: "CNY",
          updated_at: null,
          updated_by: "",
          source: "default",
        },
      ],
    });
    mockPutMediaPricing.mockResolvedValue({ pricing: [] });
    mockPatchMediaPricing.mockResolvedValue({ pricing: [] });
    mockDeleteMediaPricing.mockResolvedValue({ pricing: [] });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    queryClient.clear();
  });

  const renderPanel = async (enabled: boolean) => {
    await act(async () => {
      root.render(
        <Wrap queryClient={queryClient}>
          <ConsolePricingPanel enabled={enabled} />
        </Wrap>,
      );
    });
    // Let the async query resolve.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });
  };

  // ① Gate: enabled=false → null + fetch not called
  it("renders nothing when enabled=false and does not call fetchModelPricing", async () => {
    await renderPanel(false);
    expect(container.querySelector('[data-testid="pricing-panel"]')).toBeNull();
    expect(mockFetchModelPricing).not.toHaveBeenCalled();
  });

  // ② Admin sees all rows: union of models + pricing keys
  it("renders rows for all models (union of config models and pricing keys)", async () => {
    await renderPanel(true);

    // model-a, model-b (from pricing), model-c (from config only)
    const rows = container.querySelectorAll('[data-testid^="pricing-row-"]');
    expect(rows.length).toBe(3);
    expect(
      container.querySelector('[data-testid="pricing-row-model-a"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="pricing-row-model-b"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="pricing-row-model-c"]'),
    ).not.toBeNull();
  });

  // ⑥ source="yaml" rows show a badge
  it("shows source badge for yaml-sourced rows", async () => {
    await renderPanel(true);
    // model-b is source="yaml" — check its badge exists
    const yamlRow = container.querySelector(
      '[data-testid="pricing-row-model-b"]',
    );
    expect(yamlRow).not.toBeNull();
    // The badge text should be the yaml label (默认值 in zh-CN)
    const badge = yamlRow!.querySelector("span");
    expect(badge?.textContent).toBeTruthy();
  });

  // ③ Save only submits PRICED models — unpriced rows (input ≤ 0 AND output ≤ 0)
  // are omitted from the payload so the DB never stores 0-valued rows.
  it("omits unpriced models from save payload (A4: no 0-price rows in DB)", async () => {
    await renderPanel(true);

    const saveButton = container.querySelector('[data-testid="pricing-save"]')!;
    expect(saveButton).not.toBeNull();

    await act(async () => {
      (saveButton as HTMLElement).click();
    });

    expect(mockPutModelPricing).toHaveBeenCalledTimes(1);
    const payload = mockPutModelPricing.mock.calls[0]![0];
    const keys = Object.keys(payload.pricing);
    // model-a and model-b have real prices; model-c is unpriced (0/0) → omitted
    expect(keys).toContain("model-a");
    expect(keys).toContain("model-b");
    expect(keys).not.toContain("model-c");
    expect(keys.length).toBe(2);
  });

  // ④ cache_hit: empty → null, "0" → 0
  it("converts empty cache_hit to null and explicit 0 to 0 in payload", async () => {
    await renderPanel(true);

    // model-b has empty cache_hit → should be null in payload
    // model-a has 0.8 → should be 0.8
    const saveButton = container.querySelector('[data-testid="pricing-save"]')!;
    await act(async () => {
      (saveButton as HTMLElement).click();
    });

    const payload = mockPutModelPricing.mock.calls[0]![0];
    expect(payload.pricing["model-a"].input_cache_hit_per_million).toBe(0.8);
    expect(payload.pricing["model-b"].input_cache_hit_per_million).toBeNull();
  });

  // ⑦ A4: clearing a db-priced model triggers confirmation dialog
  it("shows confirmation when clearing db-priced models (A4 diff guard)", async () => {
    await renderPanel(true);

    // Clear model-a's prices (model-a is source="db") to make it unpriced.
    // Use React-compatible native value setter since happy-dom's event system
    // doesn't trigger React's synthetic onChange directly.
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    const inputs = container.querySelectorAll(
      '[data-testid="pricing-row-model-a"] input[type="number"]',
    );
    await act(async () => {
      nativeSetter.call(inputs[0], "");
      inputs[0]!.dispatchEvent(new Event("input", { bubbles: true }));
      nativeSetter.call(inputs[1], "");
      inputs[1]!.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const saveButton = container.querySelector('[data-testid="pricing-save"]')!;
    await act(async () => {
      (saveButton as HTMLElement).click();
    });

    // Confirmation dialog should have opened because model-a was db-priced
    // (OBS-3：确认弹层替代 window.confirm)
    expect(dialogVisible()).toBe(true);
    // User declined → save NOT called
    await clickDialog("取消");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(mockPutModelPricing).not.toHaveBeenCalled();
  });

  it("clearing db-priced models + confirm → save proceeds (OBS-3 accept branch)", async () => {
    await renderPanel(true);
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    const inputs = container.querySelectorAll(
      '[data-testid="pricing-row-model-a"] input[type="number"]',
    );
    await act(async () => {
      nativeSetter.call(inputs[0], "");
      inputs[0]!.dispatchEvent(new Event("input", { bubbles: true }));
      nativeSetter.call(inputs[1], "");
      inputs[1]!.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const saveButton = container.querySelector('[data-testid="pricing-save"]')!;
    await act(async () => {
      (saveButton as HTMLElement).click();
    });
    expect(dialogVisible()).toBe(true);
    await clickDialog("确定");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(mockPutModelPricing).toHaveBeenCalledTimes(1);
  });

  // ⑤ 422 → toast.error
  it("shows toast.error on 422 response", async () => {
    const { toast } = await import("sonner");
    mockPutModelPricing.mockRejectedValueOnce(
      new (await import("@/core/console/api")).ConsoleApiError(
        422,
        "Mixed currencies",
      ),
    );

    await renderPanel(true);

    const saveButton = container.querySelector('[data-testid="pricing-save"]')!;
    await act(async () => {
      (saveButton as HTMLElement).click();
    });

    // Wait a tick for the rejected promise to settle
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(toast.error).toHaveBeenCalledWith("Mixed currencies");
  });

  // ⑧ Media pricing section renders and saves
  it("renders media pricing section with seed defaults", async () => {
    await renderPanel(true);
    const seedreamRow = container.querySelector(
      '[data-testid="media-pricing-row-seedream:image"]',
    );
    expect(seedreamRow).not.toBeNull();
  });

  it("does not固化 default rows when saving without edits (P2)", async () => {
    await renderPanel(true);
    const saveBtn = container.querySelector(
      '[data-testid="media-pricing-save"]',
    )!;
    await act(async () => {
      (saveBtn as HTMLElement).click();
    });
    expect(mockPutMediaPricing).toHaveBeenCalledTimes(1);
    const payload = mockPutMediaPricing.mock.calls[0]![0];
    // Both mock entries are source="default" and unedited → payload must be empty.
    // Default rows should NOT be固化 into DB.
    expect(payload.pricing).toHaveLength(0);
  });

  it("edited default row with valid price enters payload (P2)", async () => {
    await renderPanel(true);
    // Edit seedream price from 0.22 to 0.30
    const seedreamRow = container.querySelector(
      '[data-testid="media-pricing-row-seedream:image"]',
    )!;
    const input = seedreamRow.querySelector<HTMLInputElement>(
      'input[type="number"]',
    )!;
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    await act(async () => {
      setter.call(input, "0.30");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const saveBtn = container.querySelector(
      '[data-testid="media-pricing-save"]',
    )!;
    await act(async () => {
      (saveBtn as HTMLElement).click();
    });
    const payload = mockPutMediaPricing.mock.calls[0]![0];
    // Only seedream (edited), not seedance-ark (default, unedited)
    expect(payload.pricing).toHaveLength(1);
    expect(payload.pricing[0].provider).toBe("seedream");
    expect(payload.pricing[0].unit_price).toBe(0.3);
    // No audit fields
    expect(payload.pricing[0]).not.toHaveProperty("source");
  });

  // ⑨ C2 (2026-08-27 成本可信化): model_id 行内显示/编辑（空=默认行）
  it("model-level row renders model id with distinct testid", async () => {
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [
        {
          provider: "seedance",
          model_id: "bytedance/seedance-2-0-fast",
          media_type: "video",
          unit: "per_kilo_token",
          unit_price: 0.02775,
          currency: "CNY",
          updated_at: null,
          updated_by: "",
          source: "db",
        },
      ],
    });
    await renderPanel(true);
    const modelRow = container.querySelector(
      '[data-testid="media-pricing-row-seedance:bytedance/seedance-2-0-fast:video"]',
    );
    expect(modelRow).not.toBeNull();
    const modelInput = modelRow!.querySelector<HTMLInputElement>(
      'input[data-testid="media-pricing-model-seedance:bytedance/seedance-2-0-fast:video"]',
    )!;
    expect(modelInput.value).toBe("bytedance/seedance-2-0-fast");
  });

  it("editing model_id of a db row carries into payload", async () => {
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [
        {
          provider: "seedance",
          media_type: "video",
          unit: "per_kilo_token",
          unit_price: 0.046,
          currency: "CNY",
          updated_at: "2026-01-01T00:00:00Z",
          updated_by: "admin",
          source: "db",
        },
      ],
    });
    await renderPanel(true);
    const row = container.querySelector(
      '[data-testid="media-pricing-row-seedance:video"]',
    )!;
    const input = row.querySelector<HTMLInputElement>(
      'input[data-testid="media-pricing-model-seedance:video"]',
    )!;
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    await act(async () => {
      setter.call(input, "bytedance/seedance-2-0-fast");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const saveBtn = container.querySelector(
      '[data-testid="media-pricing-save"]',
    )!;
    await act(async () => {
      (saveBtn as HTMLElement).click();
    });
    const payload = mockPutMediaPricing.mock.calls[0]![0];
    expect(payload.pricing).toHaveLength(1);
    expect(payload.pricing[0].model_id).toBe("bytedance/seedance-2-0-fast");
    expect(payload.pricing[0].provider).toBe("seedance");
  });

  it("unedited provider-level db row saves with model_id null", async () => {
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [
        {
          provider: "seedance",
          media_type: "video",
          unit: "per_kilo_token",
          unit_price: 0.046,
          currency: "CNY",
          updated_at: "2026-01-01T00:00:00Z",
          updated_by: "admin",
          source: "db",
        },
      ],
    });
    await renderPanel(true);
    const saveBtn = container.querySelector(
      '[data-testid="media-pricing-save"]',
    )!;
    await act(async () => {
      (saveBtn as HTMLElement).click();
    });
    const payload = mockPutMediaPricing.mock.calls[0]![0];
    expect(payload.pricing).toHaveLength(1);
    expect(payload.pricing[0].model_id).toBeNull();
  });

  it("cleared media price omits row from payload (P1: no 0-price in DB)", async () => {
    // Start with a DB row so we can test clearing it
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [
        {
          provider: "seedream",
          media_type: "image",
          unit: "per_image",
          unit_price: 0.22,
          currency: "CNY",
          updated_at: "2026-01-01T00:00:00Z",
          updated_by: "admin",
          source: "db",
        },
      ],
    });
    await renderPanel(true);
    // Clear the price field
    const seedreamRow = container.querySelector(
      '[data-testid="media-pricing-row-seedream:image"]',
    )!;
    const input = seedreamRow.querySelector<HTMLInputElement>(
      'input[type="number"]',
    )!;
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const setter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!;
    await act(async () => {
      setter.call(input, "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const saveBtn = container.querySelector(
      '[data-testid="media-pricing-save"]',
    )!;
    await act(async () => {
      (saveBtn as HTMLElement).click();
    });
    const payload = mockPutMediaPricing.mock.calls[0]![0];
    // Cleared row → omitted from payload (deletes DB row via full-table replacement)
    expect(payload.pricing).toHaveLength(0);
  });

  // ⑩ C7 (A20, 2026-08-27): 行级 PATCH/DELETE——改一行不必整表重发
  // eslint-disable-next-line @typescript-eslint/unbound-method
  const nativeValueSetter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )!.set!;

  const dbMediaRow = (
    provider: string,
    mediaType: string,
    unit: string,
    price: number,
    modelId: string | null = null,
  ) => ({
    provider,
    model_id: modelId,
    media_type: mediaType,
    unit,
    unit_price: price,
    currency: "CNY",
    updated_at: "2026-01-01T00:00:00Z",
    updated_by: "admin",
    source: "db" as const,
  });

  it("row save button appears on edit and PATCHes only that row", async () => {
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [
        dbMediaRow("seedream", "image", "per_image", 0.5),
        dbMediaRow("seedance", "video", "per_kilo_token", 0.046),
      ],
    });
    await renderPanel(true);
    // 无编辑 → 不出现行级保存按钮
    expect(
      container.querySelector('[data-testid^="media-pricing-row-save-"]'),
    ).toBeNull();

    const row = container.querySelector(
      '[data-testid="media-pricing-row-seedream:image"]',
    )!;
    const input = row.querySelector<HTMLInputElement>('input[type="number"]')!;
    await act(async () => {
      nativeValueSetter.call(input, "0.55");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const rowSave = container.querySelector(
      '[data-testid="media-pricing-row-save-seedream:image"]',
    )!;
    expect(rowSave).not.toBeNull();
    await act(async () => {
      (rowSave as HTMLElement).click();
    });

    expect(mockPatchMediaPricing).toHaveBeenCalledTimes(1);
    const body = mockPatchMediaPricing.mock.calls[0]![0];
    expect(body.confirm).toBe(false);
    expect(body.entry).toEqual({
      provider: "seedream",
      model_id: null,
      media_type: "image",
      unit: "per_image",
      unit_price: 0.55,
      currency: "CNY",
    });
    // 整表 PUT 未被触发
    expect(mockPutMediaPricing).not.toHaveBeenCalled();
  });

  it("row save 422 magnitude warning offers confirm resend", async () => {
    const { ConsoleApiError } = await import("@/core/console/api");
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [dbMediaRow("seedance-ark", "video", "per_kilo_token", 0.046)],
    });
    await renderPanel(true);
    const row = container.querySelector(
      '[data-testid="media-pricing-row-seedance-ark:video"]',
    )!;
    const input = row.querySelector<HTMLInputElement>('input[type="number"]')!;
    await act(async () => {
      nativeValueSetter.call(input, "0.5"); // 10.9× > 5× → 后端幅度告警
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    mockPatchMediaPricing.mockRejectedValueOnce(
      new ConsoleApiError(
        422,
        "Unit price changed by more than 5x on existing keys - pass confirm=true to apply: seedance-ark/* video",
      ),
    );
    const rowSave = container.querySelector(
      '[data-testid="media-pricing-row-save-seedance-ark:video"]',
    )!;
    await act(async () => {
      (rowSave as HTMLElement).click();
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    // OBS-3：422 幅度告警走弹层（window.confirm 已退役）；确认后 confirm=true 重发
    expect(dialogVisible()).toBe(true);
    await clickDialog("确定");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(mockPatchMediaPricing).toHaveBeenCalledTimes(2);
    expect(mockPatchMediaPricing.mock.calls[1]![0].confirm).toBe(true);
  });

  it("row save with invalid price toasts error and sends nothing", async () => {
    const { toast } = await import("sonner");
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [dbMediaRow("seedream", "image", "per_image", 0.5)],
    });
    await renderPanel(true);
    const row = container.querySelector(
      '[data-testid="media-pricing-row-seedream:image"]',
    )!;
    const input = row.querySelector<HTMLInputElement>('input[type="number"]')!;
    await act(async () => {
      nativeValueSetter.call(input, "");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const rowSave = container.querySelector(
      '[data-testid="media-pricing-row-save-seedream:image"]',
    )!;
    await act(async () => {
      (rowSave as HTMLElement).click();
    });
    expect(toast.error).toHaveBeenCalledWith(
      "Price must be a number greater than 0",
    );
    expect(mockPatchMediaPricing).not.toHaveBeenCalled();
  });

  it("delete button renders only for db rows and DELETEs after confirm", async () => {
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [
        dbMediaRow("seedream", "image", "per_image", 0.5),
        {
          provider: "gpt-image-2",
          model_id: null,
          media_type: "image",
          unit: "per_kilo_token",
          unit_price: 0.204,
          currency: "CNY",
          updated_at: null,
          updated_by: "",
          source: "default" as const,
        },
      ],
    });
    await renderPanel(true);
    // DB 行有删除按钮；种子行没有（不在 DB，无可删）
    expect(
      container.querySelector(
        '[data-testid="media-pricing-row-delete-seedream:image"]',
      ),
    ).not.toBeNull();
    expect(
      container.querySelector(
        '[data-testid="media-pricing-row-delete-gpt-image-2:image"]',
      ),
    ).toBeNull();

    const deleteBtn = container.querySelector(
      '[data-testid="media-pricing-row-delete-seedream:image"]',
    )!;
    await act(async () => {
      (deleteBtn as HTMLElement).click();
    });
    // OBS-3：删除确认弹层（取消分支先验证——删除不可逆）
    expect(dialogVisible()).toBe(true);
    await clickDialog("取消");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(mockDeleteMediaPricing).not.toHaveBeenCalled();

    await act(async () => {
      (deleteBtn as HTMLElement).click();
    });
    expect(dialogVisible()).toBe(true);
    await clickDialog("确定");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(mockDeleteMediaPricing).toHaveBeenCalledTimes(1);
    expect(mockDeleteMediaPricing.mock.calls[0]![0]).toEqual({
      provider: "seedream",
      mediaType: "image",
      modelId: null,
    });
  });

  it("editing model_id on row save warns about new-key upsert", async () => {
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [dbMediaRow("seedance", "video", "per_kilo_token", 0.046)],
    });
    await renderPanel(true);
    const modelInput = container.querySelector<HTMLInputElement>(
      'input[data-testid="media-pricing-model-seedance:video"]',
    )!;
    await act(async () => {
      nativeValueSetter.call(modelInput, "bytedance/seedance-2-0-fast");
      modelInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const rowSave = container.querySelector(
      '[data-testid="media-pricing-row-save-seedance:video"]',
    )!;
    await act(async () => {
      (rowSave as HTMLElement).click();
    });
    // 先告知「新键 upsert、旧键行保留」→ 确认后才发 PATCH（OBS-3 弹层）
    expect(dialogVisible()).toBe(true);
    await clickDialog("确定");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(mockPatchMediaPricing).toHaveBeenCalledTimes(1);
    expect(mockPatchMediaPricing.mock.calls[0]![0].entry.model_id).toBe(
      "bytedance/seedance-2-0-fast",
    );
  });

  it("default row with only model_id edited enters full-table payload (F3)", async () => {
    // F3（2026-08-27 评审修复）：种子行只改 model_id（不改价）也是编辑——
    // 修前整表保存把这类行当"未编辑"排除，修改静默丢失且 toast 照常成功。
    mockFetchMediaPricing.mockResolvedValue({
      pricing: [
        {
          provider: "seedream",
          model_id: null,
          media_type: "image",
          unit: "per_image",
          unit_price: 0.5,
          currency: "CNY",
          updated_at: null,
          updated_by: "",
          source: "default" as const,
        },
      ],
    });
    await renderPanel(true);
    const modelInput = container.querySelector<HTMLInputElement>(
      'input[data-testid="media-pricing-model-seedream:image"]',
    )!;
    await act(async () => {
      nativeValueSetter.call(modelInput, "doubao-seedream-5-0-pro-260628");
      modelInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const saveBtn = container.querySelector(
      '[data-testid="media-pricing-save"]',
    )!;
    await act(async () => {
      (saveBtn as HTMLElement).click();
    });
    const payload = mockPutMediaPricing.mock.calls[0]![0];
    // 以种子价为底、新 model_id 落键——不再静默丢弃
    expect(payload.pricing).toHaveLength(1);
    expect(payload.pricing[0].model_id).toBe("doubao-seedream-5-0-pro-260628");
    expect(payload.pricing[0].unit_price).toBe(0.5);
  });
});
