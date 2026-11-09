import assert from "node:assert/strict";
import fs from "node:fs/promises";
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

async function makeTempDir(prefix: string): Promise<string> {
  return fs.mkdtemp(path.join(os.tmpdir(), prefix));
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

  useEnv(t, {
    OPENAI_API_KEY: "openai-key",
    ARK_API_KEY: null,
  });
  assert.equal(detectProvider(makeArgs()), "openai");
});

test("detectProvider selects an available ref-capable provider for reference-image tasks", (t) => {
  useEnv(t, {
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
  useEnv(t, {
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
  useEnv(t, {
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
  useEnv(t, {
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

test("detectProvider prefers openai (gpt-image-2) for plain text-to-image when multiple keys exist (2026-08-08 default switch)", (t) => {
  // New default order: openai > seedream > qihu360 > azure > zai.
  // Pins the behavior change: with OPENAI+ARK+QIHU all set, default must be openai.
  useEnv(t, {
    OPENAI_API_KEY: "openai-key",
    ARK_API_KEY: "ark-key",
    QIHU_API_KEY: "qihu-key",
    AZURE_OPENAI_API_KEY: null,
    AZURE_OPENAI_BASE_URL: null,
    ZAI_API_KEY: null,
    BIGMODEL_API_KEY: null,
  });

  assert.equal(detectProvider(makeArgs()), "openai");
});

test("detectProvider auto-routes --ref to seedream even when openai is available (identity preservation, 2026-08-08)", (t) => {
  // gpt-image-2 via 360 has identity drift on reference editing; --ref must
  // prefer seedream (Ark direct) whenever ARK_API_KEY is present.
  useEnv(t, {
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
    "seedream",
  );
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
