import path from "node:path";
import process from "node:process";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { access, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import type {
  BatchFile,
  BatchTaskInput,
  CliArgs,
  ExtendConfig,
  OpenAIImageApiDialect,
  Provider,
} from "./types";

type ProviderModule = {
  getDefaultModel: () => string;
  generateImage: (prompt: string, model: string, args: CliArgs) => Promise<Uint8Array>;
  validateArgs?: (model: string, args: CliArgs) => void;
  getDefaultOutputExtension?: (model: string, args: CliArgs) => string;
};

type PreparedTask = {
  id: string;
  prompt: string;
  args: CliArgs;
  provider: Provider;
  model: string;
  outputPath: string;
  providerModule: ProviderModule;
};

type TaskResult = {
  id: string;
  provider: Provider;
  model: string;
  outputPath: string;
  success: boolean;
  attempts: number;
  error: string | null;
};

type ProviderRateLimit = {
  concurrency: number;
  startIntervalMs: number;
};

type LoadedBatchTasks = {
  tasks: BatchTaskInput[];
  jobs: number | null;
  batchDir: string;
};

const MAX_ATTEMPTS = 3;
const DEFAULT_MAX_WORKERS = 10;
const POLL_WAIT_MS = 250;
const DEFAULT_PROVIDER_RATE_LIMITS: Record<Provider, ProviderRateLimit> = {
  openai: { concurrency: 3, startIntervalMs: 1100 },
  azure: { concurrency: 3, startIntervalMs: 1100 },
  zai: { concurrency: 3, startIntervalMs: 1100 },
  seedream: { concurrency: 3, startIntervalMs: 1100 },
  qihu360: { concurrency: 3, startIntervalMs: 1100 },
};

const DEFAULT_FALLBACK_MAP: Partial<Record<Provider, Provider>> = {};

function printUsage(): void {
  console.log(`Usage:
  tsx scripts/main.ts --prompt "A cat" --image cat.png
  tsx scripts/main.ts --promptfiles system.md content.md --image out.png
  tsx scripts/main.ts --batchfile batch.json

Options:
  -p, --prompt <text>       Prompt text
  --promptfiles <files...>  Read prompt from files (concatenated)
  --image <path>            Output image path (required in single-image mode)
  --batchfile <path>        JSON batch file for multi-image generation
  --jobs <count>            Worker count for batch mode (default: auto, max from config, built-in default 10)
  --provider openai|azure|zai|seedream|qihu360  Force provider (auto-detect by default)
  -m, --model <id>          Model ID
  --ar <ratio>              Aspect ratio (e.g., 16:9, 1:1, 4:3)
  --size <WxH>              Size (e.g., 1024x1024)
  --quality normal|2k       Quality preset (default: 2k)
  --imageSize 1K|2K|4K      Image size preset
  --imageApiDialect <id>    OpenAI-compatible image dialect: openai-native|ratio-metadata
  --ref <files...>          Reference images (OpenAI, Azure, Seedream 4.0/4.5/5.0)
  --n <count>               Number of images for the current task (default: 1)
  --json                    JSON output
  -h, --help                Show help

Batch file format:
  {
    "jobs": 4,
    "tasks": [
      {
        "id": "hero",
        "promptFiles": ["prompts/hero.md"],
        "image": "out/hero.png",
        "provider": "openai",
        "model": "gpt-image-2",
        "ar": "16:9"
      }
    ]
  }

Behavior:
  - Batch mode automatically runs in parallel when pending tasks >= 2
  - Each image retries automatically up to 3 attempts
  - Batch summary reports success count, failure count, and per-image errors

Environment variables:
  OPENAI_API_KEY            OpenAI API key
  ZAI_API_KEY               Z.AI API key
  BIGMODEL_API_KEY          Backward-compatible alias for Z.AI API key
  ARK_API_KEY               Seedream/Ark API key
  OPENAI_IMAGE_MODEL        Default OpenAI model (gpt-image-2)
  ZAI_IMAGE_MODEL           Default Z.AI model (glm-image)
  BIGMODEL_IMAGE_MODEL      Backward-compatible alias for Z.AI model (glm-image)
  SEEDREAM_IMAGE_MODEL      Default Seedream model (doubao-seedream-5-0-260128)
  QIHU_API_KEY              360 API key (for qihu360 provider)
  QIHU_IMAGE_MODEL          Default 360 image model (google/gemini-3.1-flash-image-preview)
  QIHU_BASE_URL             Custom 360 API endpoint
  OPENAI_BASE_URL           Custom OpenAI endpoint
  OPENAI_IMAGE_API_DIALECT  OpenAI-compatible image dialect (openai-native|ratio-metadata)
  OPENAI_IMAGE_USE_CHAT     Use /chat/completions instead of /images/generations (true|false)
  ZAI_BASE_URL              Custom Z.AI endpoint
  BIGMODEL_BASE_URL         Backward-compatible alias for Z.AI endpoint
  AZURE_OPENAI_API_KEY      Azure OpenAI API key
  AZURE_OPENAI_BASE_URL     Azure OpenAI resource or deployment endpoint
  AZURE_OPENAI_DEPLOYMENT   Default Azure deployment name
  AZURE_API_VERSION         Azure API version (default: 2025-04-01-preview)
  AZURE_OPENAI_IMAGE_MODEL  Backward-compatible Azure deployment/model alias (defaults to gpt-image-2)
  SEEDREAM_BASE_URL         Custom Seedream endpoint
  BAOYU_IMAGE_GEN_MAX_WORKERS  Override batch worker cap
  BAOYU_IMAGE_GEN_<PROVIDER>_CONCURRENCY  Override provider concurrency
  BAOYU_IMAGE_GEN_<PROVIDER>_START_INTERVAL_MS  Override provider start gap in ms

Env file load order: CLI args > EXTEND.md > process.env > <cwd>/.baoyu-skills/.env > ~/.baoyu-skills/.env`);
}

export function parseArgs(argv: string[]): CliArgs {
  const out: CliArgs = {
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
  };

  const positional: string[] = [];

  const takeMany = (i: number): { items: string[]; next: number } => {
    const items: string[] = [];
    let j = i + 1;
    while (j < argv.length) {
      const v = argv[j]!;
      if (v.startsWith("-")) break;
      items.push(v);
      j++;
    }
    return { items, next: j - 1 };
  };

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;

    if (a === "--help" || a === "-h") {
      out.help = true;
      continue;
    }

    if (a === "--json") {
      out.json = true;
      continue;
    }

    if (a === "--prompt" || a === "-p") {
      const v = argv[++i];
      if (!v) throw new Error(`Missing value for ${a}`);
      out.prompt = v;
      continue;
    }

    if (a === "--promptfiles") {
      const { items, next } = takeMany(i);
      if (items.length === 0) throw new Error("Missing files for --promptfiles");
      out.promptFiles.push(...items);
      i = next;
      continue;
    }

    if (a === "--image") {
      const v = argv[++i];
      if (!v) throw new Error("Missing value for --image");
      out.imagePath = v;
      continue;
    }

    if (a === "--batchfile") {
      const v = argv[++i];
      if (!v) throw new Error("Missing value for --batchfile");
      out.batchFile = v;
      continue;
    }

    if (a === "--jobs") {
      const v = argv[++i];
      if (!v) throw new Error("Missing value for --jobs");
      out.jobs = parseInt(v, 10);
      if (isNaN(out.jobs) || out.jobs < 1) throw new Error(`Invalid worker count: ${v}`);
      continue;
    }

    if (a === "--provider") {
      const v = argv[++i];
      if (
        v !== "openai" &&
        v !== "azure" &&
        v !== "zai" &&
        v !== "seedream" &&
        v !== "qihu360"
      ) {
        throw new Error(`Invalid provider: ${v}`);
      }
      out.provider = v;
      continue;
    }

    if (a === "--model" || a === "-m") {
      const v = argv[++i];
      if (!v) throw new Error(`Missing value for ${a}`);
      out.model = v;
      continue;
    }

    if (a === "--ar") {
      const v = argv[++i];
      if (!v) throw new Error("Missing value for --ar");
      out.aspectRatio = v;
      out.aspectRatioSource = "cli";
      continue;
    }

    if (a === "--size") {
      const v = argv[++i];
      if (!v) throw new Error("Missing value for --size");
      out.size = v;
      continue;
    }

    if (a === "--quality") {
      const v = argv[++i];
      if (v !== "normal" && v !== "2k") throw new Error(`Invalid quality: ${v}`);
      out.quality = v;
      continue;
    }

    if (a === "--imageSize") {
      const v = argv[++i]?.toUpperCase();
      if (v !== "1K" && v !== "2K" && v !== "4K") throw new Error(`Invalid imageSize: ${v}`);
      out.imageSize = v;
      out.imageSizeSource = "cli";
      continue;
    }

    if (a === "--imageApiDialect") {
      const v = argv[++i];
      if (v !== "openai-native" && v !== "ratio-metadata") {
        throw new Error(`Invalid imageApiDialect: ${v}`);
      }
      out.imageApiDialect = v;
      continue;
    }

    if (a === "--ref" || a === "--reference") {
      const { items, next } = takeMany(i);
      if (items.length === 0) throw new Error(`Missing files for ${a}`);
      out.referenceImages.push(...items);
      i = next;
      continue;
    }

    if (a === "--n") {
      const v = argv[++i];
      if (!v) throw new Error("Missing value for --n");
      out.n = parseInt(v, 10);
      if (isNaN(out.n) || out.n < 1) throw new Error(`Invalid count: ${v}`);
      continue;
    }

    if (a.startsWith("-")) {
      throw new Error(`Unknown option: ${a}`);
    }

    positional.push(a);
  }

  if (!out.prompt && out.promptFiles.length === 0 && positional.length > 0) {
    out.prompt = positional.join(" ");
  }

  return out;
}

async function loadEnvFile(p: string): Promise<Record<string, string>> {
  try {
    const content = await readFile(p, "utf8");
    const env: Record<string, string> = {};
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const idx = trimmed.indexOf("=");
      if (idx === -1) continue;
      const key = trimmed.slice(0, idx).trim();
      let val = trimmed.slice(idx + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      env[key] = val;
    }
    return env;
  } catch {
    return {};
  }
}

async function loadEnv(): Promise<void> {
  const home = homedir();
  const cwd = process.cwd();

  const homeEnv = await loadEnvFile(path.join(home, ".baoyu-skills", ".env"));
  const cwdEnv = await loadEnvFile(path.join(cwd, ".baoyu-skills", ".env"));

  for (const [k, v] of Object.entries(homeEnv)) {
    if (!process.env[k]) process.env[k] = v;
  }
  for (const [k, v] of Object.entries(cwdEnv)) {
    if (!process.env[k]) process.env[k] = v;
  }
}

export function extractYamlFrontMatter(content: string): string | null {
  const match = content.match(/^---\s*\n([\s\S]*?)\n---\s*$/m);
  return match ? match[1] : null;
}

export function parseSimpleYaml(yaml: string): Partial<ExtendConfig> {
  const config: Partial<ExtendConfig> = {};
  const lines = yaml.split("\n");
  let currentKey: string | null = null;
  let currentProvider: Provider | null = null;

  for (const line of lines) {
    const trimmed = line.trim();
    const indent = line.match(/^\s*/)?.[0].length ?? 0;
    if (!trimmed || trimmed.startsWith("#")) continue;

    if (trimmed.includes(":") && !trimmed.startsWith("-")) {
      const colonIdx = trimmed.indexOf(":");
      const key = trimmed.slice(0, colonIdx).trim();
      let value = trimmed.slice(colonIdx + 1).trim();

      if (value === "null" || value === "") {
        value = "null";
      }

      if (key === "version") {
        config.version = value === "null" ? 1 : parseInt(value, 10);
      } else if (key === "default_provider") {
        config.default_provider = value === "null" ? null : (value as Provider);
      } else if (key === "default_quality") {
        config.default_quality = value === "null" ? null : value as "normal" | "2k";
      } else if (key === "default_aspect_ratio") {
        const cleaned = value.replace(/['"]/g, "");
        config.default_aspect_ratio = cleaned === "null" ? null : cleaned;
      } else if (key === "default_image_size") {
        config.default_image_size = value === "null" ? null : value as "1K" | "2K" | "4K";
      } else if (key === "default_image_api_dialect") {
        config.default_image_api_dialect =
          value === "null" ? null : parseOpenAIImageApiDialect(value);
      } else if (key === "default_model") {
        config.default_model = {
          openai: null,
          azure: null,
          zai: null,
          seedream: null,
          qihu360: null,
        };
        currentKey = "default_model";
        currentProvider = null;
      } else if (key === "batch") {
        config.batch = {};
        currentKey = "batch";
        currentProvider = null;
      } else if (key === "fallback") {
        config.fallback = {};
        currentKey = "fallback";
        currentProvider = null;
      } else if (currentKey === "batch" && indent >= 2 && key === "max_workers") {
        config.batch ??= {};
        config.batch.max_workers = value === "null" ? null : parseInt(value, 10);
      } else if (currentKey === "batch" && indent >= 2 && key === "provider_limits") {
        config.batch ??= {};
        config.batch.provider_limits ??= {};
        currentKey = "provider_limits";
        currentProvider = null;
      } else if (
        currentKey === "provider_limits" &&
        indent >= 4 &&
        (
          key === "openai" ||
          key === "azure" ||
          key === "zai" ||
          key === "seedream" ||
          key === "qihu360"
        )
      ) {
        config.batch ??= {};
        config.batch.provider_limits ??= {};
        config.batch.provider_limits[key] ??= {};
        currentProvider = key;
      } else if (
        currentKey === "default_model" &&
        (
          key === "openai" ||
          key === "azure" ||
          key === "zai" ||
          key === "seedream" ||
          key === "qihu360"
        )
      ) {
        const cleaned = value.replace(/['"]/g, "");
        config.default_model![key] = cleaned === "null" ? null : cleaned;
      } else if (
        currentKey === "fallback" &&
        indent >= 2 &&
        (
          key === "openai" ||
          key === "azure" ||
          key === "zai" ||
          key === "seedream" ||
          key === "qihu360"
        )
      ) {
        config.fallback ??= {};
        config.fallback[key] = value === "null" ? null : (value as Provider);
      } else if (
        currentKey === "provider_limits" &&
        currentProvider &&
        indent >= 6 &&
        (key === "concurrency" || key === "start_interval_ms")
      ) {
        config.batch ??= {};
        config.batch.provider_limits ??= {};
        const providerLimit = (config.batch.provider_limits[currentProvider] ??= {});
        if (key === "concurrency") {
          providerLimit.concurrency = value === "null" ? null : parseInt(value, 10);
        } else {
          providerLimit.start_interval_ms = value === "null" ? null : parseInt(value, 10);
        }
      }
    }
  }

  return config;
}

export function parseOpenAIImageApiDialect(
  value: string | undefined | null
): OpenAIImageApiDialect | null {
  if (!value) return null;
  const normalized = value.replace(/['"]/g, "").trim();
  if (normalized === "openai-native" || normalized === "ratio-metadata") return normalized;
  throw new Error(`Invalid OpenAI image API dialect: ${value}`);
}

type ExtendConfigPathPair = {
  current: string;
  prior?: string;
  legacy: string;
};

function getExtendConfigPathPairs(cwd: string, home: string): ExtendConfigPathPair[] {
  return [
    {
      current: path.join(cwd, ".baoyu-skills", "trainerai-imagine", "EXTEND.md"),
      prior: path.join(cwd, ".baoyu-skills", "baoyu-imagine", "EXTEND.md"),
      legacy: path.join(cwd, ".baoyu-skills", "baoyu-image-gen", "EXTEND.md"),
    },
    {
      current: path.join(home, ".baoyu-skills", "trainerai-imagine", "EXTEND.md"),
      prior: path.join(home, ".baoyu-skills", "baoyu-imagine", "EXTEND.md"),
      legacy: path.join(home, ".baoyu-skills", "baoyu-image-gen", "EXTEND.md"),
    },
  ];
}

async function exists(filePath: string): Promise<boolean> {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function migrateLegacyExtendConfig(cwd: string, home: string): Promise<void> {
  for (const { current, prior, legacy } of getExtendConfigPathPairs(cwd, home)) {
    const [hasCurrent, hasLegacy] = await Promise.all([exists(current), exists(legacy)]);
    if (hasCurrent || !hasLegacy) continue;
    const src = (prior && await exists(prior)) ? prior : legacy;
    if (!(await exists(src))) continue;
    await mkdir(path.dirname(current), { recursive: true });
    await rename(src, current);
  }
}

export async function loadExtendConfig(
  cwd = process.cwd(),
  home = homedir(),
): Promise<Partial<ExtendConfig>> {
  await migrateLegacyExtendConfig(cwd, home);

  const paths = getExtendConfigPathPairs(cwd, home).flatMap(({ current, prior }) =>
    prior ? [current, prior] : [current]
  );

  for (const p of paths) {
    try {
      const content = await readFile(p, "utf8");
      const yaml = extractYamlFrontMatter(content);
      if (!yaml) continue;
      return parseSimpleYaml(yaml);
    } catch {
      continue;
    }
  }

  return {};
}

export function mergeConfig(args: CliArgs, extend: Partial<ExtendConfig>): CliArgs {
  const aspectRatio = args.aspectRatio ?? extend.default_aspect_ratio ?? null;
  const imageSize = args.imageSize ?? extend.default_image_size ?? null;
  const imageApiDialect =
    args.imageApiDialect ??
    extend.default_image_api_dialect ??
    parseOpenAIImageApiDialect(process.env.OPENAI_IMAGE_API_DIALECT);
  return {
    ...args,
    provider: args.provider ?? extend.default_provider ?? null,
    quality: args.quality ?? extend.default_quality ?? null,
    aspectRatio,
    aspectRatioSource:
      args.aspectRatioSource ??
      (args.aspectRatio !== null ? "cli" : (aspectRatio !== null ? "config" : null)),
    imageSize,
    imageSizeSource:
      args.imageSizeSource ??
      (args.imageSize !== null ? "cli" : (imageSize !== null ? "config" : null)),
    imageApiDialect,
  };
}

export function parsePositiveInt(value: string | undefined): number | null {
  if (!value) return null;
  const parsed = parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function parsePositiveBatchInt(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    return Number.isInteger(value) && value > 0 ? value : null;
  }
  if (typeof value === "string") {
    return parsePositiveInt(value);
  }
  return null;
}

export function getConfiguredMaxWorkers(extendConfig: Partial<ExtendConfig>): number {
  const envValue = parsePositiveInt(process.env.BAOYU_IMAGE_GEN_MAX_WORKERS);
  const configValue = extendConfig.batch?.max_workers ?? null;
  return Math.max(1, envValue ?? configValue ?? DEFAULT_MAX_WORKERS);
}

export function getConfiguredProviderRateLimits(
  extendConfig: Partial<ExtendConfig>
): Record<Provider, ProviderRateLimit> {
  const configured: Record<Provider, ProviderRateLimit> = {
    openai: { ...DEFAULT_PROVIDER_RATE_LIMITS.openai },
    azure: { ...DEFAULT_PROVIDER_RATE_LIMITS.azure },
    zai: { ...DEFAULT_PROVIDER_RATE_LIMITS.zai },
    seedream: { ...DEFAULT_PROVIDER_RATE_LIMITS.seedream },
    qihu360: { ...DEFAULT_PROVIDER_RATE_LIMITS.qihu360 },
  };

  for (const provider of ["openai", "azure", "zai", "seedream", "qihu360"] as Provider[]) {
    const envPrefix = `BAOYU_IMAGE_GEN_${provider.toUpperCase()}`;
    const extendLimit = extendConfig.batch?.provider_limits?.[provider];
    configured[provider] = {
      concurrency:
        parsePositiveInt(process.env[`${envPrefix}_CONCURRENCY`]) ??
        extendLimit?.concurrency ??
        configured[provider].concurrency,
      startIntervalMs:
        parsePositiveInt(process.env[`${envPrefix}_START_INTERVAL_MS`]) ??
        extendLimit?.start_interval_ms ??
        configured[provider].startIntervalMs,
    };
  }

  return configured;
}

async function readPromptFromFiles(files: string[]): Promise<string> {
  const parts: string[] = [];
  for (const f of files) {
    parts.push(await readFile(f, "utf8"));
  }
  return parts.join("\n\n");
}

async function readPromptFromStdin(): Promise<string | null> {
  if (process.stdin.isTTY) return null;
  try {
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }
    const value = Buffer.concat(chunks).toString("utf8").trim();
    return value.length > 0 ? value : null;
  } catch {
    return null;
  }
}

export function normalizeOutputImagePath(p: string, defaultExtension = ".png"): string {
  const full = path.resolve(p);
  const ext = path.extname(full);
  if (ext) return full;
  return `${full}${defaultExtension}`;
}

function inferProviderFromModel(model: string | null): Provider | null {
  if (!model) return null;
  const normalized = model.trim();
  if (normalized.includes("seedream") || normalized.includes("seededit")) return "seedream";
  if (normalized === "glm-image" || normalized === "cogview-4-250304") return "zai";
  if (normalized.includes("gemini") || normalized.startsWith("google/")) return "qihu360";
  return null;
}

export function detectProvider(args: CliArgs): Provider {
  if (
    args.referenceImages.length > 0 &&
    args.provider &&
    args.provider !== "openai" &&
    args.provider !== "azure" &&
    args.provider !== "seedream"
  ) {
    throw new Error(
      "Reference images require a ref-capable provider. Use --provider openai (GPT Image edits), --provider azure (Azure OpenAI), or --provider seedream for supported Seedream models."
    );
  }

  if (args.provider) return args.provider;

  const hasAzure = !!(process.env.AZURE_OPENAI_API_KEY && process.env.AZURE_OPENAI_BASE_URL);
  const hasOpenai = !!process.env.OPENAI_API_KEY;
  const hasZai = !!(process.env.ZAI_API_KEY || process.env.BIGMODEL_API_KEY);
  const hasSeedream = !!process.env.ARK_API_KEY;
  const hasQihu360 = !!process.env.QIHU_API_KEY;
  const modelProvider = inferProviderFromModel(args.model);

  if (modelProvider === "seedream") {
    if (!hasSeedream) {
      throw new Error("Model looks like a Volcengine ARK image model, but ARK_API_KEY is not set.");
    }
    return "seedream";
  }

  if (modelProvider === "zai") {
    if (!hasZai) {
      throw new Error("Model looks like a Z.AI image model, but ZAI_API_KEY is not set.");
    }
    return "zai";
  }

  if (modelProvider === "qihu360") {
    if (!hasQihu360) {
      throw new Error("Model looks like a 360 image model, but QIHU_API_KEY is not set.");
    }
    return "qihu360";
  }

  if (args.referenceImages.length > 0) {
    // 2026-08-08: auto-route --ref to seedream (Ark) for identity preservation.
    // gpt-image-2 via 360 causes identity drift on multi-frame reference editing.
    if (hasSeedream) return "seedream";
    if (hasOpenai) return "openai";
    if (hasAzure) return "azure";
    throw new Error(
      "Reference images require Seedream, OpenAI, or Azure. Set ARK_API_KEY, OPENAI_API_KEY, or AZURE_OPENAI_API_KEY+AZURE_OPENAI_BASE_URL, or remove --ref."
    );
  }

  // 2026-08-08: default text-to-image prefers openai (gpt-image-2 via 360 gateway).
  const available = [
    hasOpenai && "openai",
    hasSeedream && "seedream",
    hasQihu360 && "qihu360",
    hasAzure && "azure",
    hasZai && "zai",
  ].filter(Boolean) as Provider[];

  if (available.length === 1) return available[0]!;
  if (available.length > 1) return available[0]!;

  throw new Error(
    "No API key found. Set OPENAI_API_KEY, AZURE_OPENAI_API_KEY+AZURE_OPENAI_BASE_URL, ZAI_API_KEY, ARK_API_KEY, or QIHU_API_KEY.\n" +
      "Create ~/.baoyu-skills/.env or <cwd>/.baoyu-skills/.env with your keys."
  );
}

export type ReferenceImageValidationOptions = {
  allowRemoteUrls?: boolean;
};

function isRemoteReferenceImage(refPath: string): boolean {
  return /^https?:\/\//i.test(refPath);
}

function shouldAllowRemoteReferenceImages(_provider: Provider | null): boolean {
  return false;
}

export async function validateReferenceImages(
  referenceImages: string[],
  options: ReferenceImageValidationOptions = {},
): Promise<void> {
  for (const refPath of referenceImages) {
    if (options.allowRemoteUrls && isRemoteReferenceImage(refPath)) continue;
    const fullPath = path.resolve(refPath);
    try {
      await access(fullPath);
    } catch {
      throw new Error(`Reference image not found: ${fullPath}`);
    }
  }
}

export function isRetryableGenerationError(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : String(error);
  const nonRetryableMarkers = [
    "Reference image",
    "not supported",
    "only supported",
    "No API key found",
    "is required",
    "Invalid ",
    "Unexpected ",
    "API error (400)",
    "API error (401)",
    "API error (402)",
    "API error (403)",
    "API error (404)",
    "temporarily disabled",
    "supports saving exactly one image",
    "supports only",
    "support exactly one output image",
    "support aspect ratios in",
    "requires total pixels between",
    "accept at most",
  ];
  return !nonRetryableMarkers.some((marker) => msg.includes(marker));
}

async function loadProviderModule(provider: Provider): Promise<ProviderModule> {
  if (provider === "zai") return (await import("./providers/zai")) as ProviderModule;
  if (provider === "seedream") return (await import("./providers/seedream")) as ProviderModule;
  if (provider === "qihu360") return (await import("./providers/qihu360")) as ProviderModule;
  if (provider === "azure") return (await import("./providers/azure")) as ProviderModule;
  return (await import("./providers/openai")) as ProviderModule;
}

async function loadPromptForArgs(args: CliArgs): Promise<string | null> {
  let prompt: string | null = args.prompt;
  if (!prompt && args.promptFiles.length > 0) {
    prompt = await readPromptFromFiles(args.promptFiles);
  }
  return prompt;
}

function getModelForProvider(
  provider: Provider,
  requestedModel: string | null,
  extendConfig: Partial<ExtendConfig>,
  providerModule: ProviderModule
): string {
  if (requestedModel) return requestedModel;
  if (extendConfig.default_model) {
    if (provider === "openai" && extendConfig.default_model.openai) return extendConfig.default_model.openai;
    if (provider === "azure" && extendConfig.default_model.azure) return extendConfig.default_model.azure;
    if (provider === "zai" && extendConfig.default_model.zai) return extendConfig.default_model.zai;
    if (provider === "seedream" && extendConfig.default_model.seedream) return extendConfig.default_model.seedream;
    if (provider === "qihu360" && extendConfig.default_model.qihu360) return extendConfig.default_model.qihu360;
  }
  return providerModule.getDefaultModel();
}

async function prepareSingleTask(args: CliArgs, extendConfig: Partial<ExtendConfig>): Promise<PreparedTask> {
  if (!args.quality) args.quality = "2k";

  const prompt = (await loadPromptForArgs(args)) ?? (await readPromptFromStdin());
  if (!prompt) throw new Error("Prompt is required");
  if (!args.imagePath) throw new Error("--image is required");
  if (args.referenceImages.length > 0) {
    await validateReferenceImages(args.referenceImages, {
      allowRemoteUrls: shouldAllowRemoteReferenceImages(args.provider),
    });
  }

  const provider = detectProvider(args);
  const providerModule = await loadProviderModule(provider);
  const model = getModelForProvider(provider, args.model, extendConfig, providerModule);
  providerModule.validateArgs?.(model, args);
  const defaultOutputExtension = providerModule.getDefaultOutputExtension?.(model, args) ?? ".png";

  return {
    id: "single",
    prompt,
    args,
    provider,
    model,
    outputPath: normalizeOutputImagePath(args.imagePath, defaultOutputExtension),
    providerModule,
  };
}

export async function loadBatchTasks(batchFilePath: string): Promise<LoadedBatchTasks> {
  const resolvedBatchFilePath = path.resolve(batchFilePath);
  const content = await readFile(resolvedBatchFilePath, "utf8");
  const parsed = JSON.parse(content.replace(/^\uFEFF/, "")) as BatchFile;
  const batchDir = path.dirname(resolvedBatchFilePath);
  if (Array.isArray(parsed)) {
    return {
      tasks: parsed,
      jobs: null,
      batchDir,
    };
  }
  if (parsed && typeof parsed === "object" && Array.isArray(parsed.tasks)) {
    const jobs = parsePositiveBatchInt(parsed.jobs);
    if (parsed.jobs !== undefined && parsed.jobs !== null && jobs === null) {
      throw new Error("Invalid batch file. jobs must be a positive integer when provided.");
    }
    return {
      tasks: parsed.tasks,
      jobs,
      batchDir,
    };
  }
  throw new Error("Invalid batch file. Expected an array of tasks or an object with a tasks array.");
}

export function resolveBatchPath(batchDir: string, filePath: string): string {
  return path.isAbsolute(filePath) ? filePath : path.resolve(batchDir, filePath);
}

function resolveBatchReferencePath(batchDir: string, filePath: string): string {
  return isRemoteReferenceImage(filePath) ? filePath : resolveBatchPath(batchDir, filePath);
}

export function createTaskArgs(baseArgs: CliArgs, task: BatchTaskInput, batchDir: string): CliArgs {
  return {
    ...baseArgs,
    prompt: task.prompt ?? null,
    promptFiles: task.promptFiles ? task.promptFiles.map((filePath) => resolveBatchPath(batchDir, filePath)) : [],
    imagePath: task.image ? resolveBatchPath(batchDir, task.image) : null,
    provider: task.provider ?? baseArgs.provider ?? null,
    model: task.model ?? baseArgs.model ?? null,
    aspectRatio: task.ar ?? baseArgs.aspectRatio ?? null,
    aspectRatioSource: task.ar != null ? "task" : (baseArgs.aspectRatioSource ?? null),
    size: task.size ?? baseArgs.size ?? null,
    quality: task.quality ?? baseArgs.quality ?? null,
    imageSize: task.imageSize ?? baseArgs.imageSize ?? null,
    imageSizeSource: task.imageSize != null ? "task" : (baseArgs.imageSizeSource ?? null),
    imageApiDialect: task.imageApiDialect ?? baseArgs.imageApiDialect ?? null,
    referenceImages: task.ref ? task.ref.map((filePath) => resolveBatchReferencePath(batchDir, filePath)) : [],
    n: task.n ?? baseArgs.n,
    batchFile: null,
    jobs: baseArgs.jobs,
    json: baseArgs.json,
    help: false,
  };
}

async function prepareBatchTasks(
  args: CliArgs,
  extendConfig: Partial<ExtendConfig>
): Promise<{ tasks: PreparedTask[]; jobs: number | null }> {
  if (!args.batchFile) throw new Error("--batchfile is required in batch mode");
  const { tasks: taskInputs, jobs: batchJobs, batchDir } = await loadBatchTasks(args.batchFile);
  if (taskInputs.length === 0) throw new Error("Batch file does not contain any tasks.");

  const prepared: PreparedTask[] = [];
  for (let i = 0; i < taskInputs.length; i++) {
    const task = taskInputs[i]!;
    const taskArgs = createTaskArgs(args, task, batchDir);
    const prompt = await loadPromptForArgs(taskArgs);
    if (!prompt) throw new Error(`Task ${i + 1} is missing prompt or promptFiles.`);
    if (!taskArgs.imagePath) throw new Error(`Task ${i + 1} is missing image output path.`);
    if (taskArgs.referenceImages.length > 0) {
      await validateReferenceImages(taskArgs.referenceImages, {
        allowRemoteUrls: shouldAllowRemoteReferenceImages(taskArgs.provider),
      });
    }

    const provider = detectProvider(taskArgs);
    const providerModule = await loadProviderModule(provider);
    const model = getModelForProvider(provider, taskArgs.model, extendConfig, providerModule);
    providerModule.validateArgs?.(model, taskArgs);
    const defaultOutputExtension = providerModule.getDefaultOutputExtension?.(model, taskArgs) ?? ".png";
    prepared.push({
      id: task.id || `task-${String(i + 1).padStart(2, "0")}`,
      prompt,
      args: taskArgs,
      provider,
      model,
      outputPath: normalizeOutputImagePath(taskArgs.imagePath, defaultOutputExtension),
      providerModule,
    });
  }

  return {
    tasks: prepared,
    jobs: args.jobs ?? batchJobs,
  };
}

async function writeImage(outputPath: string, imageData: Uint8Array): Promise<void> {
  await mkdir(path.dirname(outputPath), { recursive: true });
  await writeFile(outputPath, imageData);
}

async function generatePreparedTask(
  task: PreparedTask,
  extendConfig: Partial<ExtendConfig>,
): Promise<TaskResult> {
  console.error(`Using ${task.provider} / ${task.model} for ${task.id}`);
  console.error(
    `Switch model: --model <id> | EXTEND.md default_model.${task.provider} | env ${task.provider.toUpperCase()}_IMAGE_MODEL`
  );

  let attempts = 0;
  while (attempts < MAX_ATTEMPTS) {
    attempts += 1;
    try {
      const imageData = await task.providerModule.generateImage(task.prompt, task.model, task.args);
      await writeImage(task.outputPath, imageData);
      return {
        id: task.id,
        provider: task.provider,
        model: task.model,
        outputPath: task.outputPath,
        success: true,
        attempts,
        error: null,
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const canRetry = attempts < MAX_ATTEMPTS && isRetryableGenerationError(error);
      if (canRetry) {
        console.error(`[${task.id}] Attempt ${attempts}/${MAX_ATTEMPTS} failed, retrying...`);
        continue;
      }
      break;
    }
  }

  // Try fallback provider
  const fallbackConfig = extendConfig.fallback ?? {};
  const fallbackProvider = fallbackConfig[task.provider] ?? DEFAULT_FALLBACK_MAP[task.provider];
  if (fallbackProvider) {
    console.error(`[${task.id}] ${task.provider} failed, falling back to ${fallbackProvider}...`);
    const fallbackModule = await loadProviderModule(fallbackProvider);
    const fallbackModel = getModelForProvider(fallbackProvider, task.args.model, extendConfig, fallbackModule);
    fallbackModule.validateArgs?.(fallbackModel, task.args);

    console.error(`Using ${fallbackProvider} / ${fallbackModel} for ${task.id} (fallback)`);
    let fbAttempts = 0;
    while (fbAttempts < MAX_ATTEMPTS) {
      fbAttempts += 1;
      try {
        const imageData = await fallbackModule.generateImage(task.prompt, fallbackModel, task.args);
        await writeImage(task.outputPath, imageData);
        return {
          id: task.id,
          provider: fallbackProvider,
          model: fallbackModel,
          outputPath: task.outputPath,
          success: true,
          attempts: fbAttempts,
          error: null,
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const canRetry = fbAttempts < MAX_ATTEMPTS && isRetryableGenerationError(error);
        if (canRetry) {
          console.error(`[${task.id}] Fallback ${fallbackProvider} attempt ${fbAttempts}/${MAX_ATTEMPTS} failed, retrying...`);
          continue;
        }
        return {
          id: task.id,
          provider: fallbackProvider,
          model: fallbackModel,
          outputPath: task.outputPath,
          success: false,
          attempts: fbAttempts,
          error: `Fallback ${fallbackProvider} also failed: ${message}`,
        };
      }
    }
  }

  return {
    id: task.id,
    provider: task.provider,
    model: task.model,
    outputPath: task.outputPath,
    success: false,
    attempts: MAX_ATTEMPTS,
    error: `${task.provider} generation failed after ${MAX_ATTEMPTS} attempts`,
  };
}

function createProviderGate(providerRateLimits: Record<Provider, ProviderRateLimit>) {
  const state = new Map<Provider, { active: number; lastStartedAt: number }>();

  return async function acquire(provider: Provider): Promise<() => void> {
    const limit = providerRateLimits[provider];
    while (true) {
      const current = state.get(provider) ?? { active: 0, lastStartedAt: 0 };
      const now = Date.now();
      const enoughCapacity = current.active < limit.concurrency;
      const enoughGap = now - current.lastStartedAt >= limit.startIntervalMs;
      if (enoughCapacity && enoughGap) {
        state.set(provider, { active: current.active + 1, lastStartedAt: now });
        return () => {
          const latest = state.get(provider) ?? { active: 1, lastStartedAt: now };
          state.set(provider, {
            active: Math.max(0, latest.active - 1),
            lastStartedAt: latest.lastStartedAt,
          });
        };
      }
      await new Promise((resolve) => setTimeout(resolve, POLL_WAIT_MS));
    }
  };
}

export function getWorkerCount(taskCount: number, jobs: number | null, maxWorkers: number): number {
  const requested = jobs ?? Math.min(taskCount, maxWorkers);
  return Math.max(1, Math.min(requested, taskCount, maxWorkers));
}

async function runBatchTasks(
  tasks: PreparedTask[],
  jobs: number | null,
  extendConfig: Partial<ExtendConfig>
): Promise<TaskResult[]> {
  if (tasks.length === 1) {
    return [await generatePreparedTask(tasks[0]!, extendConfig)];
  }

  const maxWorkers = getConfiguredMaxWorkers(extendConfig);
  const providerRateLimits = getConfiguredProviderRateLimits(extendConfig);
  const acquireProvider = createProviderGate(providerRateLimits);
  const workerCount = getWorkerCount(tasks.length, jobs, maxWorkers);
  console.error(`Batch mode: ${tasks.length} tasks, ${workerCount} workers, parallel mode enabled.`);
  for (const provider of ["openai", "azure", "zai", "seedream", "qihu360"] as Provider[]) {
    const limit = providerRateLimits[provider];
    console.error(`- ${provider}: concurrency=${limit.concurrency}, startIntervalMs=${limit.startIntervalMs}`);
  }

  let nextIndex = 0;
  const results: TaskResult[] = new Array(tasks.length);

  const worker = async (): Promise<void> => {
    while (true) {
      const currentIndex = nextIndex;
      nextIndex += 1;
      if (currentIndex >= tasks.length) return;

      const task = tasks[currentIndex]!;
      const release = await acquireProvider(task.provider);
      try {
        results[currentIndex] = await generatePreparedTask(task, extendConfig);
      } finally {
        release();
      }
    }
  };

  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return results;
}

function printBatchSummary(results: TaskResult[]): void {
  const successCount = results.filter((result) => result.success).length;
  const failureCount = results.length - successCount;

  console.error("");
  console.error("Batch generation summary:");
  console.error(`- Total: ${results.length}`);
  console.error(`- Succeeded: ${successCount}`);
  console.error(`- Failed: ${failureCount}`);

  if (failureCount > 0) {
    console.error("Failure reasons:");
    for (const result of results.filter((item) => !item.success)) {
      console.error(`- ${result.id}: ${result.error}`);
    }
  }
}

function emitJson(payload: unknown): void {
  console.log(JSON.stringify(payload, null, 2));
}

async function runSingleMode(args: CliArgs, extendConfig: Partial<ExtendConfig>): Promise<void> {
  const task = await prepareSingleTask(args, extendConfig);
  const result = await generatePreparedTask(task, extendConfig);
  if (!result.success) {
    throw new Error(result.error || "Generation failed");
  }

  if (args.json) {
    emitJson({
      savedImage: result.outputPath,
      provider: result.provider,
      model: result.model,
      attempts: result.attempts,
      prompt: task.prompt.slice(0, 200),
    });
    return;
  }

  console.log(result.outputPath);
}

async function runBatchMode(args: CliArgs, extendConfig: Partial<ExtendConfig>): Promise<void> {
  const { tasks, jobs } = await prepareBatchTasks(args, extendConfig);
  const results = await runBatchTasks(tasks, jobs, extendConfig);
  printBatchSummary(results);

  if (args.json) {
    emitJson({
      mode: "batch",
      total: results.length,
      succeeded: results.filter((item) => item.success).length,
      failed: results.filter((item) => !item.success).length,
      results,
    });
  }

  if (results.some((item) => !item.success)) {
    process.exitCode = 1;
  }
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printUsage();
    return;
  }

  await loadEnv();
  const extendConfig = await loadExtendConfig();
  const mergedArgs = mergeConfig(args, extendConfig);
  if (!mergedArgs.quality) mergedArgs.quality = "2k";

  if (mergedArgs.batchFile) {
    await runBatchMode(mergedArgs, extendConfig);
    return;
  }

  await runSingleMode(mergedArgs, extendConfig);
}

function isDirectExecution(metaUrl: string): boolean {
  const entryPath = process.argv[1];
  if (!entryPath) return false;

  try {
    return path.resolve(entryPath) === fileURLToPath(metaUrl);
  } catch {
    return false;
  }
}

if (isDirectExecution(import.meta.url)) {
  main().catch((error) => {
    const message = error instanceof Error ? error.message : String(error);
    console.error(message);
    process.exit(1);
  });
}
