import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";
import test, { type TestContext } from "node:test";

import type { CliArgs, ExtendConfig } from "./types.ts";
import {
  createTaskArgs,
  detectProvider,
  getConfiguredMaxWorkers,
  getConfiguredProviderRateLimits,
  getWorkerCount,
  isRetryableGenerationError,
  loadBatchTasks,
  loadExtendConfig,
  mergeConfig,
  normalizeOutputImagePath,
  parseArgs,
  parseOpenAIImageApiDialect,
  parseSimpleYaml,
  validateReferenceImages,
} from "./main.ts";
import {
  buildGenerationsBody,
  generateImage,
  getDefaultModel,
} from "./providers/qihu360.ts";

function makeArgs(overrides: Partial<CliArgs> = {}): CliArgs {
  return {
    prompt: null,
    promptFiles: [],
    imagePath: null,
    provider: null,
    model: null,
    aspectRatio: null,
    aspectRatioSource: null,
    size: null,
    quality: null,
    imageSize: null,
    imageSizeSource: null,
    imageApiDialect: null,
    referenceImages: [],
    n: 1,
    batchFile: null,
    jobs: null,
    json: false,
    help: false,
    ...overrides,
  };
}

function useEnv(
  t: TestContext,
  values: Record<string, string | null>,
): void {
  const previous = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(values)) {
    previous.set(key, process.env[key]);
    if (value == null) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }

  t.after(() => {
    for (const [key, value] of previous.entries()) {
      if (value == null) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  });
}

const PROVIDER_ENV_KEYS = [
  "OPENAI_API_KEY",
  "OPENAI_BASE_URL",
  "OPENAI_IMAGE_MODEL",
  "OPENAI_IMAGE_USE_CHAT",
  "AZURE_OPENAI_API_KEY",
  "AZURE_OPENAI_BASE_URL",
  "AZURE_OPENAI_DEPLOYMENT",
  "AZURE_OPENAI_IMAGE_MODEL",
  "AZURE_API_VERSION",
  "ZAI_API_KEY",
  "BIGMODEL_API_KEY",
  "ZAI_IMAGE_MODEL",
  "BIGMODEL_IMAGE_MODEL",
  "ZAI_BASE_URL",
  "BIGMODEL_BASE_URL",
  "ARK_API_KEY",
  "SEEDREAM_IMAGE_MODEL",
  "SEEDREAM_BASE_URL",
  "QIHU_API_KEY",
  "QIHU_IMAGE_MODEL",
  "QIHU_BASE_URL",
] as const;

function useProviderEnv(
  t: TestContext,
  values: Record<string, string | null> = {},
): void {
  const cleared = Object.fromEntries(
    PROVIDER_ENV_KEYS.map((key) => [key, null]),
  ) as Record<string, string | null>;
  useEnv(t, { ...cleared, ...values });
}

function captureWarnings(t: TestContext): string[] {
  const warnings: string[] = [];
  const originalWarn = console.warn;
  console.warn = (...args: unknown[]) => {
    warnings.push(args.map(String).join(" "));
  };
  t.after(() => {
    console.warn = originalWarn;
  });
  return warnings;
}

const PNG_BYTES = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10]);
const PNG_BASE64 = "iVBORw0KGgo=";
const JPEG_BYTES = Uint8Array.from([255, 216, 255, 224]);

async function makeTempDir(prefix: string): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), prefix));
}

async function makeQihuStub(t: TestContext, responseStatus = 200) {
  let requestCount = 0;
  const server = createServer((request, response) => {
    requestCount += 1;
    request.resume();
    request.on("end", () => {
      response.writeHead(responseStatus, {
        "Content-Type": "application/json",
        Connection: "close",
      });
      response.end(responseStatus === 200
        ? JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] })
        : "stub failure");
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  t.after(() => new Promise<void>((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  }));

  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("QIHU stub did not bind to a TCP port");
  }
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    get requestCount() {
      return requestCount;
    },
  };
}

function getCurrentTsxRuntimeArgs(): string[] {
  const runtimeArgs: string[] = [];
  for (let i = 0; i < process.execArgv.length; i++) {
    const flag = process.execArgv[i]!;
    const value = process.execArgv[i + 1];
    if (
      (flag === "--require" || flag === "--import") &&
      value &&
      (value === "tsx" || /[\\/]tsx[\\/]/.test(value))
    ) {
      runtimeArgs.push(flag, value);
      i += 1;
    }
  }
  if (runtimeArgs.length === 0) {
    throw new Error("CLI tests must run with tsx so the same loader can be reused");
  }
  return runtimeArgs;
}

function runMainCli(
  args: string[],
  cwd: string,
  qihuBaseUrl: string,
): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    execFile(
      process.execPath,
      [
        ...getCurrentTsxRuntimeArgs(),
        path.join(import.meta.dirname, "main.ts"),
        ...args,
      ],
      {
        cwd,
        env: {
          PATH: process.env.PATH ?? "",
          HOME: cwd,
          QIHU_API_KEY: "offline-test-key",
          QIHU_BASE_URL: qihuBaseUrl,
          QIHU_IMAGE_MODEL: "bytedance/doubao-seedream-5.0-pro",
        },
        timeout: 30_000,
      },
      (error, stdout, stderr) => {
        if (error && typeof error.code !== "number") {
          reject(error);
          return;
        }
        resolve({
          code: typeof error?.code === "number" ? error.code : 0,
          stdout,
          stderr,
        });
      },
    );
  });
}

test("parseArgs parses the main trainerai-imagine CLI flags", () => {
  const args = parseArgs([
    "--promptfiles",
    "prompts/system.md",
    "prompts/content.md",
    "--image",
    "out/hero",
    "--provider",
    "zai",
    "--quality",
    "2k",
    "--imageSize",
    "4k",
    "--imageApiDialect",
    "ratio-metadata",
    "--ref",
    "ref/one.png",
    "ref/two.jpg",
    "--n",
    "3",
    "--jobs",
    "5",
    "--json",
  ]);

  assert.deepEqual(args.promptFiles, ["prompts/system.md", "prompts/content.md"]);
  assert.equal(args.imagePath, "out/hero");
  assert.equal(args.provider, "zai");
  assert.equal(args.quality, "2k");
  assert.equal(args.aspectRatioSource, null);
  assert.equal(args.imageSize, "4K");
  assert.equal(args.imageSizeSource, "cli");
  assert.equal(args.imageApiDialect, "ratio-metadata");
  assert.deepEqual(args.referenceImages, ["ref/one.png", "ref/two.jpg"]);
  assert.equal(args.n, 3);
  assert.equal(args.jobs, 5);
  assert.equal(args.json, true);
});

test("parseArgs falls back to positional prompt and rejects invalid provider", () => {
  const positional = parseArgs(["draw", "a", "cat"]);
  assert.equal(positional.prompt, "draw a cat");

  assert.throws(
    () => parseArgs(["--provider", "stability"]),
    /Invalid provider/,
  );
});

test("validateReferenceImages can skip remote URLs for providers that support them", async () => {
  await validateReferenceImages(["https://example.com/ref.png"], { allowRemoteUrls: true });

  await assert.rejects(
    () => validateReferenceImages(["https://example.com/ref.png"]),
    /Reference image not found/,
  );
});

test("parseSimpleYaml parses nested defaults and provider limits", () => {
  const yaml = `
version: 2
default_provider: openai
default_quality: normal
default_aspect_ratio: '16:9'
default_image_size: 2K
default_image_api_dialect: ratio-metadata
default_model:
  openai: gpt-image-2
  zai: glm-image
  azure: image-prod
batch:
  max_workers: 8
  provider_limits:
    openai:
      concurrency: 4
    zai:
      concurrency: 2
      start_interval_ms: 1000
    azure:
      concurrency: 1
      start_interval_ms: 1500
`;

  const config = parseSimpleYaml(yaml);

  assert.equal(config.version, 2);
  assert.equal(config.default_provider, "openai");
  assert.equal(config.default_quality, "normal");
  assert.equal(config.default_aspect_ratio, "16:9");
  assert.equal(config.default_image_size, "2K");
  assert.equal(config.default_image_api_dialect, "ratio-metadata");
  assert.equal(config.default_model?.openai, "gpt-image-2");
  assert.equal(config.default_model?.zai, "glm-image");
  assert.equal(config.default_model?.azure, "image-prod");
  assert.equal(config.batch?.max_workers, 8);
  assert.deepEqual(config.batch?.provider_limits?.openai, {
    concurrency: 4,
  });
  assert.deepEqual(config.batch?.provider_limits?.zai, {
    concurrency: 2,
    start_interval_ms: 1000,
  });
  assert.deepEqual(config.batch?.provider_limits?.azure, {
    concurrency: 1,
    start_interval_ms: 1500,
  });
});

test("loadExtendConfig renames legacy EXTEND.md when the new path is missing", async () => {
  const root = await makeTempDir("trainerai-imagine-extend-");
  const cwd = path.join(root, "project");
  const home = path.join(root, "home");
  const legacyPath = path.join(cwd, ".baoyu-skills", "baoyu-image-gen", "EXTEND.md");
  const currentPath = path.join(cwd, ".baoyu-skills", "trainerai-imagine", "EXTEND.md");

  await fs.mkdir(path.dirname(legacyPath), { recursive: true });
  await fs.mkdir(home, { recursive: true });
  await fs.writeFile(legacyPath, `---
default_provider: seedream
default_quality: 2k
---
`);

  const config = await loadExtendConfig(cwd, home);

  assert.equal(config.default_provider, "seedream");
  assert.equal(config.default_quality, "2k");
  await fs.access(currentPath);
  await assert.rejects(() => fs.access(legacyPath));
});

test("loadExtendConfig leaves legacy EXTEND.md untouched when both paths exist", async () => {
  const root = await makeTempDir("trainerai-imagine-extend-dual-");
  const cwd = path.join(root, "project");
  const home = path.join(root, "home");
  const legacyPath = path.join(cwd, ".baoyu-skills", "baoyu-image-gen", "EXTEND.md");
  const currentPath = path.join(cwd, ".baoyu-skills", "trainerai-imagine", "EXTEND.md");

  await fs.mkdir(path.dirname(legacyPath), { recursive: true });
  await fs.mkdir(path.dirname(currentPath), { recursive: true });
  await fs.mkdir(home, { recursive: true });
  await fs.writeFile(legacyPath, `---
default_provider: azure
---
`);
  await fs.writeFile(currentPath, `---
default_provider: openai
---
`);

  const config = await loadExtendConfig(cwd, home);

  assert.equal(config.default_provider, "openai");
  assert.equal(await fs.readFile(legacyPath, "utf8"), `---
default_provider: azure
---
`);
  assert.equal(await fs.readFile(currentPath, "utf8"), `---
default_provider: openai
---
`);
});

test("loadExtendConfig reads from prior baoyu-imagine path as fallback", async () => {
  const root = await makeTempDir("trainerai-imagine-prior-");
  const cwd = path.join(root, "project");
  const home = path.join(root, "home");
  const priorPath = path.join(cwd, ".baoyu-skills", "baoyu-imagine", "EXTEND.md");
  const currentPath = path.join(cwd, ".baoyu-skills", "trainerai-imagine", "EXTEND.md");

  await fs.mkdir(path.dirname(priorPath), { recursive: true });
  await fs.mkdir(home, { recursive: true });
  await fs.writeFile(priorPath, `---
default_provider: azure
default_quality: hd
---
`);

  const config = await loadExtendConfig(cwd, home);

  assert.equal(config.default_provider, "azure");
  assert.equal(config.default_quality, "hd");
});

test("mergeConfig only fills values missing from CLI args", () => {
  const merged = mergeConfig(
    makeArgs({
      provider: "openai",
      quality: null,
      aspectRatio: null,
      imageSize: "4K",
    }),
    {
      default_provider: "zai",
      default_quality: "2k",
      default_aspect_ratio: "3:2",
      default_image_size: "2K",
      default_image_api_dialect: "ratio-metadata",
    } satisfies Partial<ExtendConfig>,
  );

  assert.equal(merged.provider, "openai");
  assert.equal(merged.quality, "2k");
  assert.equal(merged.aspectRatio, "3:2");
  assert.equal(merged.aspectRatioSource, "config");
  assert.equal(merged.imageSize, "4K");
  assert.equal(merged.imageSizeSource, "cli");
  assert.equal(merged.imageApiDialect, "ratio-metadata");
});

test("mergeConfig tags inherited imageSize defaults so providers can ignore incompatible config", () => {
  const merged = mergeConfig(
    makeArgs(),
    {
      default_image_size: "2K",
    } satisfies Partial<ExtendConfig>,
  );

  assert.equal(merged.imageSize, "2K");
  assert.equal(merged.imageSizeSource, "config");
});

test("mergeConfig falls back to OPENAI_IMAGE_API_DIALECT when CLI and EXTEND are unset", (t) => {
  useEnv(t, {
    OPENAI_IMAGE_API_DIALECT: "ratio-metadata",
  });

  const merged = mergeConfig(makeArgs(), {});
  assert.equal(merged.imageApiDialect, "ratio-metadata");
});

test("parseOpenAIImageApiDialect validates supported values", () => {
  assert.equal(parseOpenAIImageApiDialect("openai-native"), "openai-native");
  assert.equal(parseOpenAIImageApiDialect("ratio-metadata"), "ratio-metadata");
  assert.equal(parseOpenAIImageApiDialect(null), null);
  assert.throws(
    () => parseOpenAIImageApiDialect("gateway-magic"),
    /Invalid OpenAI image API dialect/,
  );
});

test("detectProvider rejects non-ref-capable providers and prefers first available provider when multiple keys exist", (t) => {
  assert.throws(
    () =>
      detectProvider(
        makeArgs({
          provider: "zai",
          referenceImages: ["ref.png"],
        }),
      ),
    /Reference images require a ref-capable provider/,
  );

  useProviderEnv(t, {
    OPENAI_API_KEY: "openai-key",
    ARK_API_KEY: null,
  });
  assert.equal(detectProvider(makeArgs()), "openai");
});

test("detectProvider selects an available ref-capable provider for reference-image tasks", (t) => {
  useProviderEnv(t, {
    OPENAI_API_KEY: "openai-key",
    AZURE_OPENAI_API_KEY: null,
    AZURE_OPENAI_BASE_URL: null,
    ARK_API_KEY: null,
  });
  assert.equal(
    detectProvider(makeArgs({ referenceImages: ["ref.png"] })),
    "openai",
  );
});

test("detectProvider selects Azure when only Azure credentials are configured", (t) => {
  useProviderEnv(t, {
    OPENAI_API_KEY: null,
    AZURE_OPENAI_API_KEY: "azure-key",
    AZURE_OPENAI_BASE_URL: "https://example.openai.azure.com",
    ARK_API_KEY: null,
  });

  assert.equal(detectProvider(makeArgs()), "azure");
  assert.equal(
    detectProvider(makeArgs({ referenceImages: ["ref.png"] })),
    "azure",
  );
});

test("detectProvider selects Z.AI when credentials are present or the model id matches", (t) => {
  useProviderEnv(t, {
    OPENAI_API_KEY: null,
    AZURE_OPENAI_API_KEY: null,
    AZURE_OPENAI_BASE_URL: null,
    ZAI_API_KEY: "zai-key",
    BIGMODEL_API_KEY: null,
    ARK_API_KEY: null,
  });

  assert.equal(detectProvider(makeArgs()), "zai");
  assert.equal(detectProvider(makeArgs({ model: "glm-image" })), "zai");
});

test("detectProvider infers Seedream from model id and allows Seedream reference-image workflows", (t) => {
  useProviderEnv(t, {
    OPENAI_API_KEY: null,
    ARK_API_KEY: "ark-key",
  });

  assert.equal(
    detectProvider(
      makeArgs({
        model: "doubao-seedream-4-5-251128",
        referenceImages: ["ref.png"],
      }),
    ),
    "seedream",
  );

  assert.equal(
    detectProvider(
      makeArgs({
        provider: "seedream",
        referenceImages: ["ref.png"],
      }),
    ),
    "seedream",
  );
});

test("detectProvider prefers qihu360 for plain text-to-image when QIHU is present (2026-11-16 default switch)", (t) => {
  // 2026-11-16: 默认配图通道收敛为 360 Seedream（bytedance/doubao-seedream-5.0-pro）。
  // QIHU+OPENAI+ARK 同时存在时默认仍必须是 qihu360。
  useProviderEnv(t, {
    OPENAI_API_KEY: "openai-key",
    ARK_API_KEY: "ark-key",
    QIHU_API_KEY: "qihu-key",
    AZURE_OPENAI_API_KEY: null,
    AZURE_OPENAI_BASE_URL: null,
    ZAI_API_KEY: null,
    BIGMODEL_API_KEY: null,
  });

  assert.equal(detectProvider(makeArgs()), "qihu360");
});

test("detectProvider infers OpenAI from an explicit gpt-image model instead of applying the QIHU default", (t) => {
  useProviderEnv(t, {
    QIHU_API_KEY: "qihu-key",
    OPENAI_API_KEY: "openai-key",
  });

  assert.equal(detectProvider(makeArgs({ model: "gpt-image-2" })), "openai");
  assert.equal(
    detectProvider(makeArgs({ model: "openai/gpt-image-1.5" })),
    "openai",
  );
});

test("detectProvider falls back to Azure for an explicit gpt-image model when OpenAI is unavailable", (t) => {
  useProviderEnv(t, {
    QIHU_API_KEY: "qihu-key",
    AZURE_OPENAI_API_KEY: "azure-key",
    AZURE_OPENAI_BASE_URL: "https://example.openai.azure.com",
  });

  assert.equal(detectProvider(makeArgs({ model: "gpt-image-2" })), "azure");
});

test("detectProvider does not send an explicit gpt-image model to QIHU when OpenAI and Azure are unavailable", (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });

  assert.throws(
    () => detectProvider(makeArgs({ model: "gpt-image-2" })),
    /OPENAI_API_KEY/,
  );
});

test("detectProvider auto-routes --ref to qihu360 when QIHU is available", (t) => {
  useProviderEnv(t, {
    OPENAI_API_KEY: "openai-key",
    ARK_API_KEY: "ark-key",
    QIHU_API_KEY: "qihu-key",
    AZURE_OPENAI_API_KEY: null,
    AZURE_OPENAI_BASE_URL: null,
    ZAI_API_KEY: null,
    BIGMODEL_API_KEY: null,
  });

  assert.equal(
    detectProvider(makeArgs({ referenceImages: ["ref.png"] })),
    "qihu360",
  );
});

test("detectProvider infers qihu360 from bytedance seedream model ids", (t) => {
  useProviderEnv(t, {
    QIHU_API_KEY: "qihu-key",
    OPENAI_API_KEY: null,
    ARK_API_KEY: null,
    AZURE_OPENAI_API_KEY: null,
    AZURE_OPENAI_BASE_URL: null,
    ZAI_API_KEY: null,
    BIGMODEL_API_KEY: null,
  });

  assert.equal(
    detectProvider(makeArgs({ model: "bytedance/doubao-seedream-5.0-pro" })),
    "qihu360",
  );
});

test("detectProvider rejects Gemini/google and explicit non-Seedream QIHU models before generation setup", (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });

  for (const model of [
    "google/gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
  ]) {
    assert.throws(
      () => detectProvider(makeArgs({ model })),
      /qihu360 supports only Seedream models/,
    );
  }
  assert.throws(
    () => detectProvider(makeArgs({ provider: "qihu360", model: "gpt-image-2" })),
    /qihu360 supports only Seedream models/,
  );
});

test("detectProvider keeps an explicit --provider over the QIHU default", (t) => {
  useProviderEnv(t, {
    QIHU_API_KEY: "qihu-key",
    OPENAI_API_KEY: "openai-key",
    ARK_API_KEY: null,
    AZURE_OPENAI_API_KEY: null,
    AZURE_OPENAI_BASE_URL: null,
    ZAI_API_KEY: null,
    BIGMODEL_API_KEY: null,
  });

  assert.equal(detectProvider(makeArgs({ provider: "openai" })), "openai");
});

test("detectProvider allows explicit qihu360 reference-image edits", (t) => {
  useProviderEnv(t, {
    QIHU_API_KEY: "qihu-key",
    OPENAI_API_KEY: null,
    ARK_API_KEY: null,
    AZURE_OPENAI_API_KEY: null,
    AZURE_OPENAI_BASE_URL: null,
    ZAI_API_KEY: null,
    BIGMODEL_API_KEY: null,
  });

  assert.equal(
    detectProvider(
      makeArgs({ provider: "qihu360", referenceImages: ["ref.png"] }),
    ),
    "qihu360",
  );
});

test("qihu360 body requests PNG in extra_body and maps sizes to the three Seedream tiers", () => {
  const model = "bytedance/doubao-seedream-5.0-pro";
  const landscape = buildGenerationsBody("prompt", model, makeArgs({ aspectRatio: "16:9" }));
  assert.deepEqual(landscape, {
    model,
    prompt: "prompt",
    extra_body: { size: "2048x1152", watermark: false, output_format: "png" },
  });
  assert.equal("size" in landscape, false);
  assert.equal("sequential_image_generation" in landscape, false);

  const square = buildGenerationsBody("prompt", model, makeArgs({ aspectRatio: "1:1" }));
  assert.deepEqual(square.extra_body, {
    size: "2048x2048",
    watermark: false,
    output_format: "png",
  });

  const portrait = buildGenerationsBody("prompt", model, makeArgs({ aspectRatio: "9:16" }));
  assert.deepEqual(portrait.extra_body, {
    size: "1152x2048",
    watermark: false,
    output_format: "png",
  });
});

test("qihu360 warns that quality=normal still uses a 2K tier", (t) => {
  const warnings = captureWarnings(t);
  const body = buildGenerationsBody(
    "prompt",
    "bytedance/doubao-seedream-5.0-pro",
    makeArgs({ quality: "normal", aspectRatio: "16:9" }),
  );

  assert.equal((body.extra_body as { size: string }).size, "2048x1152");
  assert.equal(warnings.length, 1);
  assert.match(warnings[0]!, /quality=normal/);
  assert.match(warnings[0]!, /2K/);
});

test("qihu360 explicit size takes precedence over aspect ratio before tier mapping and warns about normalization", (t) => {
  const warnings = captureWarnings(t);
  const body = buildGenerationsBody(
    "prompt",
    "bytedance/doubao-seedream-5.0-pro",
    makeArgs({ aspectRatio: "9:16", size: "1600x900" }),
  );

  assert.equal(
    (body.extra_body as { size: string }).size,
    "2048x1152",
  );
  assert.equal(warnings.length, 1);
  assert.match(warnings[0]!, /1600x900/);
  assert.match(warnings[0]!, /2048x1152/);
});

test("qihu360 warns when an aspect ratio is normalized but not for an exact tier ratio", (t) => {
  const warnings = captureWarnings(t);
  const model = "bytedance/doubao-seedream-5.0-pro";
  const normalized = buildGenerationsBody(
    "prompt",
    model,
    makeArgs({ aspectRatio: "4:3" }),
  );

  assert.equal((normalized.extra_body as { size: string }).size, "2048x2048");
  assert.equal(warnings.length, 1);
  assert.match(warnings[0]!, /4:3/);
  assert.match(warnings[0]!, /2048x2048/);

  warnings.length = 0;
  buildGenerationsBody("prompt", model, makeArgs({ aspectRatio: "16:9" }));
  assert.deepEqual(warnings, []);
});

test("qihu360 default model is the 360 Seedream 5.0 Pro id", (t) => {
  useProviderEnv(t);
  assert.equal(getDefaultModel(), "bytedance/doubao-seedream-5.0-pro");
});

test("qihu360 getDefaultModel rejects non-Seedream environment overrides with a compatibility error", (t) => {
  useProviderEnv(t);

  for (const model of [
    "google/gemini-3.1-flash-image-preview",
    "gpt-image-2",
  ]) {
    process.env.QIHU_IMAGE_MODEL = model;
    assert.throws(
      () => getDefaultModel(),
      /QIHU_IMAGE_MODEL supports only Seedream models/,
    );
  }
});

test("qihu360 generateImage posts generations, downloads the image, and never sends top-level size", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(input), init: init ?? {} });
    if (calls.length === 1) {
      return new Response(
        JSON.stringify({ data: [{ url: "https://cdn.example/x.png" }] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response(PNG_BYTES, {
      status: 200,
      headers: { "Content-Type": "image/png" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const bytes = await generateImage(
    "a course cover",
    getDefaultModel(),
    makeArgs({ aspectRatio: "16:9" }),
  );

  assert.deepEqual([...bytes], [...PNG_BYTES]);
  assert.equal(calls.length, 2);
  assert.equal(calls[0]!.url, "https://api.360.cn/v1/images/generations");
  assert.equal(calls[1]!.url, "https://cdn.example/x.png");
  const body = JSON.parse(String(calls[0]!.init.body));
  assert.equal(body.model, "bytedance/doubao-seedream-5.0-pro");
  assert.deepEqual(body.extra_body, {
    size: "2048x1152",
    watermark: false,
    output_format: "png",
  });
  assert.equal("size" in body, false);
  assert.equal("sequential_image_generation" in body, false);
});

test("qihu360 downloads the URL when a response also contains non-image b64_json", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const calls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request) => {
    const url = String(input);
    calls.push(url);
    if (calls.length === 1) {
      return new Response(JSON.stringify({
        data: [
          {
            url: "https://cdn.example/usable.png",
            b64_json: Buffer.alloc(33, 0x41).toString("base64"),
          },
        ],
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(PNG_BYTES, {
      status: 200,
      headers: { "Content-Type": "image/png" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const bytes = await generateImage("prompt", getDefaultModel(), makeArgs());

  assert.deepEqual([...bytes], [...PNG_BYTES]);
  assert.deepEqual(calls, [
    "https://api.360.cn/v1/images/generations",
    "https://cdn.example/usable.png",
  ]);
});

const urlFailureCases: Array<{
  name: string;
  respond: () => Promise<Response>;
}> = [
  {
    name: "HTTP status failure",
    respond: async () => new Response("unavailable", {
      status: 503,
      statusText: "Service Unavailable",
    }),
  },
  {
    name: "network failure",
    respond: async () => {
      throw Object.assign(new Error("connect timeout"), {
        cause: { code: "UND_ERR_CONNECT_TIMEOUT" },
      });
    },
  },
  {
    name: "response stream failure",
    respond: async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      arrayBuffer: () => Promise.reject(new Error("stream reset mid-body")),
    }) as unknown as Response,
  },
];

for (const failure of urlFailureCases) {
  test(`qihu360 falls back to valid b64_json after URL ${failure.name}`, async (t) => {
    useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
    const calls: string[] = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = String(input);
      calls.push(url);
      if (calls.length === 1) {
        return new Response(JSON.stringify({
          data: [
            {
              url: "https://cdn.example/unavailable.png",
              b64_json: PNG_BASE64,
            },
          ],
        }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return failure.respond();
    }) as typeof fetch;
    t.after(() => {
      globalThis.fetch = originalFetch;
    });

    const bytes = await generateImage("prompt", getDefaultModel(), makeArgs());

    assert.deepEqual([...bytes], [...PNG_BYTES]);
    assert.deepEqual(calls, [
      "https://api.360.cn/v1/images/generations",
      "https://cdn.example/unavailable.png",
    ]);
  });
}

test("qihu360 rejects non-Seedream models before any request", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  let calls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    calls += 1;
    return new Response(JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  await assert.rejects(
    generateImage("prompt", "google/gemini-3.1-flash-image-preview", makeArgs()),
    /supports only Seedream models/,
  );
  await assert.rejects(
    generateImage("prompt", "gpt-image-2", makeArgs()),
    /supports only Seedream models/,
  );
  assert.equal(calls, 0);
});

test("qihu360 rejects every image count other than one before any request", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  let calls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    calls += 1;
    return new Response(JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  for (const n of [0, 2, 4]) {
    await assert.rejects(
      generateImage("prompt", getDefaultModel(), makeArgs({ n })),
      /exactly one output image/,
    );
  }
  assert.equal(calls, 0);
});

test("qihu360 rejects JPEG bytes instead of returning them for saving as PNG", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        data: [{ b64_json: Buffer.from(JPEG_BYTES).toString("base64") }],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  await assert.rejects(
    generateImage("prompt", getDefaultModel(), makeArgs()),
    /JPEG data; expected PNG magic bytes/,
  );
});

test("qihu360 edits request PNG output in extra_body", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  let outputFormat: unknown;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_input: string | URL | Request, init?: RequestInit) => {
    const requestBody = JSON.parse(String(init?.body));
    outputFormat = requestBody.extra_body.output_format;
    return new Response(JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  await generateImage(
    "edit",
    getDefaultModel(),
    makeArgs({ referenceImages: ["data:image/png;base64,AQID"] }),
  );

  assert.equal(outputFormat, "png");
});

test("qihu360 edits encode PNG, JPEG, BMP, and TIFF references with the right MIME", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const tmp = await makeTempDir("qihu-ref-");
  t.after(() => fs.rm(tmp, { recursive: true, force: true }));
  const first = path.join(tmp, "a.png");
  const second = path.join(tmp, "b.jpg");
  const bmp = path.join(tmp, "c.bmp");
  const tiff = path.join(tmp, "d.tiff");
  const tif = path.join(tmp, "e.tif");
  await fs.writeFile(first, Buffer.from([1, 2, 3]));
  await fs.writeFile(second, Buffer.from([4, 5, 6]));
  await fs.writeFile(bmp, Buffer.from([7, 8, 9]));
  await fs.writeFile(tiff, Buffer.from([10, 11, 12]));
  await fs.writeFile(tif, Buffer.from([13, 14, 15]));

  const calls: Array<{ url: string; init: RequestInit }> = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(input), init: init ?? {} });
    return new Response(JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const single = await generateImage(
    "edit",
    getDefaultModel(),
    makeArgs({ referenceImages: [first] }),
  );
  assert.deepEqual([...single], [...PNG_BYTES]);
  assert.equal(calls[0]!.url, "https://api.360.cn/v1/images/edits");
  const singleBody = JSON.parse(String(calls[0]!.init.body));
  assert.equal(typeof singleBody.extra_body.image, "string");
  assert.match(singleBody.extra_body.image, /^data:image\/png;base64,/);

  const multiple = await generateImage(
    "edit",
    getDefaultModel(),
    makeArgs({ referenceImages: [first, second, bmp, tiff, tif] }),
  );
  assert.deepEqual([...multiple], [...PNG_BYTES]);
  assert.equal(calls[1]!.url, "https://api.360.cn/v1/images/edits");
  const multiBody = JSON.parse(String(calls[1]!.init.body));
  assert.equal(Array.isArray(multiBody.extra_body.image), true);
  assert.equal(multiBody.extra_body.image.length, 5);
  assert.match(multiBody.extra_body.image[1], /^data:image\/jpeg;base64,/);
  assert.match(multiBody.extra_body.image[2], /^data:image\/bmp;base64,/);
  assert.match(multiBody.extra_body.image[3], /^data:image\/tiff;base64,/);
  assert.match(multiBody.extra_body.image[4], /^data:image\/tiff;base64,/);
});

test("qihu360 rejects unknown local reference extensions before fetch", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const root = await makeTempDir("qihu-unknown-ref-");
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const reference = path.join(root, "reference.svg");
  await fs.writeFile(reference, "<svg xmlns=\"http://www.w3.org/2000/svg\"/>");
  let calls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    calls += 1;
    return new Response(JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  await assert.rejects(
    generateImage("edit", getDefaultModel(), makeArgs({ referenceImages: [reference] })),
    /Reference image.*unsupported extension.*\.svg/,
  );
  assert.equal(calls, 0);
});

test("qihu360 rejects local references over 10 MB before reading and uploading them", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const root = await makeTempDir("qihu-large-ref-");
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const reference = path.join(root, "large.png");
  await fs.writeFile(reference, "");
  await fs.truncate(reference, 10 * 1024 * 1024 + 1);
  let calls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    calls += 1;
    return new Response(JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  await assert.rejects(
    generateImage("edit", getDefaultModel(), makeArgs({ referenceImages: [reference] })),
    /Reference image.*exceeds 10 MB/,
  );
  assert.equal(calls, 0);
});

test("qihu360 rejects local references whose combined size exceeds 20 MB before fetch", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const root = await makeTempDir("qihu-total-ref-size-");
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const references: string[] = [];
  for (let index = 0; index < 3; index++) {
    const reference = path.join(root, `reference-${index}.png`);
    await fs.writeFile(reference, "");
    await fs.truncate(reference, 7 * 1024 * 1024);
    references.push(reference);
  }
  let calls = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => {
    calls += 1;
    return new Response(JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  await assert.rejects(
    generateImage("edit", getDefaultModel(), makeArgs({ referenceImages: references })),
    /Reference images.*exceed 20 MB total/,
  );
  assert.equal(calls, 0);
});

test("qihu360 single-image CLI rejects a JPG output before fetch and defaults an extensionless output to PNG", async (t) => {
  const root = await makeTempDir("trainerai-imagine-single-output-");
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const stub = await makeQihuStub(t);
  const jpgPath = path.join(root, "cover.jpg");

  const rejected = await runMainCli(
    [
      "--provider",
      "qihu360",
      "--model",
      "bytedance/doubao-seedream-5.0-pro",
      "--prompt",
      "course cover",
      "--image",
      jpgPath,
    ],
    root,
    stub.baseUrl,
  );

  assert.notEqual(rejected.code, 0);
  assert.match(rejected.stderr, /qihu360 supports only PNG output paths/);
  assert.equal(stub.requestCount, 0);
  await assert.rejects(fs.access(jpgPath));

  const extensionlessPath = path.join(root, "cover");
  const generated = await runMainCli(
    [
      "--provider",
      "qihu360",
      "--model",
      "bytedance/doubao-seedream-5.0-pro",
      "--prompt",
      "course cover",
      "--image",
      extensionlessPath,
    ],
    root,
    stub.baseUrl,
  );

  assert.equal(generated.code, 0, generated.stderr);
  assert.equal(stub.requestCount, 1);
  const saved = await fs.readFile(`${extensionlessPath}.png`);
  assert.deepEqual([...saved], [...PNG_BYTES]);
});

test("qihu360 batch CLI rejects a WebP output during preparation without fetching", async (t) => {
  const root = await makeTempDir("trainerai-imagine-batch-output-");
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const stub = await makeQihuStub(t);
  const webpPath = path.join(root, "cover.webp");
  const batchPath = path.join(root, "batch.json");
  await fs.writeFile(batchPath, JSON.stringify({
    tasks: [
      {
        id: "cover",
        provider: "qihu360",
        model: "bytedance/doubao-seedream-5.0-pro",
        prompt: "course cover",
        image: webpPath,
      },
    ],
  }));

  const result = await runMainCli(
    ["--batchfile", batchPath, "--json"],
    root,
    stub.baseUrl,
  );

  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /qihu360 supports only PNG output paths/);
  assert.equal(stub.requestCount, 0);
  await assert.rejects(fs.access(webpPath));
});

test("qihu360 CLI retries a server failure three times and rejects n!=1 without another request", async (t) => {
  const root = await makeTempDir("trainerai-imagine-attempts-");
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const stub = await makeQihuStub(t, 500);
  const baseArgs = [
    "--provider",
    "qihu360",
    "--model",
    "bytedance/doubao-seedream-5.0-pro",
    "--prompt",
    "course cover",
  ];
  const outputPath = path.join(root, "attempt.png");

  const failed = await runMainCli(
    [...baseArgs, "--image", outputPath],
    root,
    stub.baseUrl,
  );

  assert.notEqual(failed.code, 0);
  assert.match(failed.stderr, /generation failed after 3 attempts/);
  assert.equal(stub.requestCount, 3);
  await assert.rejects(fs.access(outputPath));

  const multiOutputPath = path.join(root, "multi.png");
  const rejected = await runMainCli(
    [...baseArgs, "--image", multiOutputPath, "--n", "2"],
    root,
    stub.baseUrl,
  );

  assert.notEqual(rejected.code, 0);
  assert.match(rejected.stderr, /exactly one output image/);
  assert.equal(stub.requestCount, 3);
  await assert.rejects(fs.access(multiOutputPath));
});

test("qihu360 CLI stops after one attempt for a non-retryable API error", async (t) => {
  const root = await makeTempDir("trainerai-imagine-non-retryable-");
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const stub = await makeQihuStub(t, 400);
  const outputPath = path.join(root, "attempt.png");

  const failed = await runMainCli(
    [
      "--provider",
      "qihu360",
      "--model",
      "bytedance/doubao-seedream-5.0-pro",
      "--prompt",
      "course cover",
      "--image",
      outputPath,
    ],
    root,
    stub.baseUrl,
  );

  assert.notEqual(failed.code, 0);
  assert.equal(stub.requestCount, 1);
  assert.match(failed.stderr, /generation failed after 1 attempts/);
  await assert.rejects(fs.access(outputPath));
});

test("batch worker and provider-rate-limit configuration prefer env over EXTEND config", (t) => {
  useEnv(t, {
    BAOYU_IMAGE_GEN_MAX_WORKERS: "12",
    BAOYU_IMAGE_GEN_OPENAI_CONCURRENCY: "5",
    BAOYU_IMAGE_GEN_OPENAI_START_INTERVAL_MS: "450",
    BAOYU_IMAGE_GEN_ZAI_CONCURRENCY: "4",
  });

  const extendConfig: Partial<ExtendConfig> = {
    batch: {
      max_workers: 7,
      provider_limits: {
        openai: {
          concurrency: 2,
          start_interval_ms: 900,
        },
        zai: {
          concurrency: 1,
          start_interval_ms: 1200,
        },
      },
    },
  };

  assert.equal(getConfiguredMaxWorkers(extendConfig), 12);
  assert.deepEqual(getConfiguredProviderRateLimits(extendConfig).openai, {
    concurrency: 5,
    startIntervalMs: 450,
  });
  assert.deepEqual(getConfiguredProviderRateLimits(extendConfig).zai, {
    concurrency: 4,
    startIntervalMs: 1200,
  });
});

test("loadBatchTasks and createTaskArgs resolve batch-relative paths", async (t) => {
  const root = await makeTempDir("trainerai-imagine-batch-");
  t.after(() => fs.rm(root, { recursive: true, force: true }));

  const batchFile = path.join(root, "jobs", "batch.json");
  await fs.mkdir(path.dirname(batchFile), { recursive: true });
  await fs.writeFile(
    batchFile,
    JSON.stringify({
      jobs: 2,
      tasks: [
        {
          id: "hero",
          promptFiles: ["prompts/hero.md"],
          image: "out/hero",
          ref: ["refs/hero.png", "https://example.com/ref.png"],
          ar: "16:9",
        },
      ],
    }),
  );

  const loaded = await loadBatchTasks(batchFile);
  assert.equal(loaded.jobs, 2);
  assert.equal(loaded.batchDir, path.dirname(batchFile));
  assert.equal(loaded.tasks[0]?.id, "hero");

  const taskArgs = createTaskArgs(
    makeArgs({
      provider: "openai",
      quality: "2k",
      imageApiDialect: "ratio-metadata",
      json: true,
    }),
    loaded.tasks[0]!,
    loaded.batchDir,
  );

  assert.deepEqual(taskArgs.promptFiles, [
    path.join(loaded.batchDir, "prompts/hero.md"),
  ]);
  assert.equal(taskArgs.imagePath, path.join(loaded.batchDir, "out/hero"));
  assert.deepEqual(taskArgs.referenceImages, [
    path.join(loaded.batchDir, "refs/hero.png"),
    "https://example.com/ref.png",
  ]);
  assert.equal(taskArgs.provider, "openai");
  assert.equal(taskArgs.aspectRatio, "16:9");
  assert.equal(taskArgs.quality, "2k");
  assert.equal(taskArgs.imageApiDialect, "ratio-metadata");
  assert.equal(taskArgs.json, true);
});

test("path normalization, worker count, and retry classification follow expected rules", () => {
  assert.match(normalizeOutputImagePath("out/sample"), /out[\\/]+sample\.png$/);
  assert.match(normalizeOutputImagePath("out/sample", ".jpg"), /out[\\/]+sample\.jpg$/);
  assert.match(normalizeOutputImagePath("out/sample.webp"), /out[\\/]+sample\.webp$/);

  assert.equal(getWorkerCount(8, null, 3), 3);
  assert.equal(getWorkerCount(2, 6, 5), 2);
  assert.equal(getWorkerCount(5, 0, 4), 1);

  assert.equal(isRetryableGenerationError(new Error("API error (401): denied")), false);
  assert.equal(
    isRetryableGenerationError(
      new Error("Replicate returned 2 outputs, but trainerai-imagine currently supports saving exactly one image per request."),
    ),
    false,
  );
  assert.equal(
    isRetryableGenerationError(
      new Error("Provider image models accept at most 9 reference images. Received 10."),
    ),
    false,
  );
  assert.equal(
    isRetryableGenerationError(
      new Error("Provider image models in trainerai-imagine support exactly one output image per request."),
    ),
    false,
  );
  assert.equal(
    isRetryableGenerationError(
      new Error("Provider image models support aspect ratios in [1:8, 8:1]."),
    ),
    false,
  );
  assert.equal(
    isRetryableGenerationError(
      new Error("Provider image model requires total pixels between 768*768 and 2048*2048."),
    ),
    false,
  );
  assert.equal(isRetryableGenerationError(new Error("socket hang up")), true);
});

// ── 媒体计量账本（2026-11-23 首页六卡媒体计量计划 PR 3）─────────────────────
// qihu360 在 360 返回含图之后、CDN 下载之前向线程工作区账本追加一行 image 事件；
// 发射永不抛错（同步 fs 调用包在 try/catch 内，不留未 await 的 Promise）。

const IMAGE_EVENT_KEYS = ["elapsed_ms", "event_id", "height", "kind", "model", "width"];

function useLedgerDir(t: TestContext, dir: string): string {
  useEnv(t, { DEER_FLOW_MEDIA_LEDGER_DIR: dir });
  return path.join(dir, "media-usage.jsonl");
}

async function readLedger(file: string): Promise<Array<Record<string, unknown>>> {
  try {
    await fs.access(file);
  } catch {
    return [];
  }
  const text = await fs.readFile(file, "utf8");
  return text.split("\n").filter(Boolean).map((line) => JSON.parse(line) as Record<string, unknown>);
}

function stubQihuFetch(
  t: TestContext,
  body: unknown,
  options: { downloads?: "fail" | "png" } = {},
): void {
  const originalFetch = globalThis.fetch;
  let call = 0;
  globalThis.fetch = (async () => {
    call += 1;
    if (call === 1) {
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (options.downloads === "fail") throw new Error("cdn unreachable");
    return new Response(PNG_BYTES, { status: 200, headers: { "Content-Type": "image/png" } });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
}

test("qihu360 records one image ledger event with only contract fields after a 200 with image", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const ledger = useLedgerDir(t, await makeTempDir("ledger-ok-"));
  stubQihuFetch(t, { data: [{ b64_json: PNG_BASE64 }] });

  const bytes = await generateImage("prompt", getDefaultModel(), makeArgs({ aspectRatio: "16:9" }));

  assert.deepEqual([...bytes], [...PNG_BYTES]);
  const events = await readLedger(ledger);
  assert.equal(events.length, 1);
  const event = events[0]!;
  assert.equal(event.kind, "image");
  assert.equal(event.model, getDefaultModel());
  assert.deepEqual([event.width, event.height], [2048, 1152]);
  assert.match(String(event.event_id), /^[0-9a-f]{32}$/);
  assert.equal(typeof event.elapsed_ms, "number");
  assert.deepEqual(Object.keys(event).sort(), IMAGE_EVENT_KEYS);
});

test("qihu360 records the model actually sent when another Seedream model is requested", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const ledger = useLedgerDir(t, await makeTempDir("ledger-model-"));
  let sentModel: string | undefined;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (_input: unknown, init?: RequestInit) => {
    sentModel = (JSON.parse(String(init?.body)) as { model: string }).model;
    return new Response(JSON.stringify({ data: [{ b64_json: PNG_BASE64 }] }), { status: 200 });
  }) as typeof fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  const model = "doubao-seedream-5-0-pro-260628"; // 非默认 Seedream

  await generateImage("prompt", model, makeArgs());

  assert.equal(sentModel, model);
  const events = await readLedger(ledger);
  assert.equal(events.length, 1);
  assert.equal(events[0]!.model, model, "事件 model 与实际下发一致，不改写为默认模型");
});

test("qihu360 writes no ledger event for a 200 without an image", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const ledger = useLedgerDir(t, await makeTempDir("ledger-empty-"));
  stubQihuFetch(t, { data: [] });

  await assert.rejects(generateImage("prompt", getDefaultModel(), makeArgs()));
  assert.deepEqual(await readLedger(ledger), []);
});

test("qihu360 keeps the ledger event when the CDN download fails", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const ledger = useLedgerDir(t, await makeTempDir("ledger-cdn-"));
  stubQihuFetch(t, { data: [{ url: "https://cdn.example/x.png" }] }, { downloads: "fail" });

  await assert.rejects(generateImage("prompt", getDefaultModel(), makeArgs()));
  const events = await readLedger(ledger);
  assert.equal(events.length, 1, "供应商已返回含图的 200，消耗已发生");
  assert.equal(events[0]!.kind, "image");
});

test("qihu360 still returns the image when the ledger directory is unwritable", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const dir = await makeTempDir("ledger-sealed-");
  const blocker = path.join(dir, "blocker.txt");
  await fs.writeFile(blocker, "regular file, not a dir");
  useEnv(t, { DEER_FLOW_MEDIA_LEDGER_DIR: path.join(blocker, ".usage") });
  stubQihuFetch(t, { data: [{ b64_json: PNG_BASE64 }] });

  const bytes = await generateImage("prompt", getDefaultModel(), makeArgs());

  assert.deepEqual([...bytes], [...PNG_BYTES]);
});

test("qihu360 ledger file is 0644 in a 0755 directory even under umask 077", async (t) => {
  useProviderEnv(t, { QIHU_API_KEY: "qihu-key" });
  const ledger = useLedgerDir(t, await makeTempDir("ledger-umask-"));
  stubQihuFetch(t, { data: [{ b64_json: PNG_BASE64 }] });
  const previous = process.umask(0o077);
  try {
    await generateImage("prompt", getDefaultModel(), makeArgs());
  } finally {
    process.umask(previous);
  }

  assert.equal((await fs.stat(ledger)).mode & 0o777, 0o644);
  assert.equal((await fs.stat(path.dirname(ledger))).mode & 0o777, 0o755);
});
