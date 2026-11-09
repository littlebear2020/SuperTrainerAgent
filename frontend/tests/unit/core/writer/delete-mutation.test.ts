import { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { describe, expect, it, vi } from "vitest";

import { deleteWriterDocMutationOptions } from "@/core/writer/hooks";

vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
vi.mock("@/core/writer/api", () => ({
  deleteWriterDoc: vi.fn().mockRejectedValue(new Error("403")),
}));

describe("deleteWriterDocMutationOptions", () => {
  it("shows an error toast on failure (P1-11: delete must not swallow)", async () => {
    const qc = new QueryClient();
    const opts = deleteWriterDocMutationOptions(qc);
    await expect(opts.mutationFn("doc1")).rejects.toThrow("403");
    opts.onError?.(new Error("403"));
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("删除失败"),
    );
  });
});
