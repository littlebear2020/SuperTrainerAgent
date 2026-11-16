import path from "node:path";
import { readFile } from "node:fs/promises";
import type { CliArgs, OpenAIImageApiDialect } from "../types";

// 2026-11-16: TrainerAI 默认生图通道 = 360 Seedream 5.0 Pro。
const DEFAULT_MODEL = "bytedance/doubao-seedream-5.0-pro";

// 360/Seedream 精确三档（16:9 / 1:1 / 9:16）；顶层 size 会被网关静默改派，
// 所以尺寸一律下发到 extra_body.size，且只落这三档。
const SIZE_TIERS = [
  [2048, 1152],
  [2048, 2048],
  [1152, 2048],
] as const;

export function getDefaultModel(): string {
  return process.env.QIHU_IMAGE_MODEL || DEFAULT_MODEL;
}

type Qihu360ImageResponse = { data: Array<{ url?: string; b64_json?: string }> };
type Qihu360GenerationsBody = Record<string, unknown>;

function getApiKey(): string | null {
  return process.env.QIHU_API_KEY || null;
}

function getBaseUrl(): string {
  return (process.env.QIHU_BASE_URL || "https://api.360.cn").replace(/\/+$/g, "");
}

export function getQihu360ImageApiDialect(args: Pick<CliArgs, "imageApiDialect">): OpenAIImageApiDialect {
  return args.imageApiDialect ?? "openai-native";
}

function parseRatio(value: string | null): number | null {
  if (!value) return null;
  const match = /^(\d+(?:\.\d+)?)[x:](\d+(?:\.\d+)?)$/i.exec(value.trim());
  if (!match) return null;
  const width = Number.parseFloat(match[1]!);
  const height = Number.parseFloat(match[2]!);
  if (!(width > 0) || !(height > 0)) return null;
  return width / height;
}

function resolveSize(aspectRatio: string | null, explicitSize: string | null): string {
  const ratio = parseRatio(explicitSize) ?? parseRatio(aspectRatio) ?? 1;
  let best: (typeof SIZE_TIERS)[number] = SIZE_TIERS[0];
  for (const tier of SIZE_TIERS) {
    if (Math.abs(tier[0] / tier[1] - ratio) < Math.abs(best[0] / best[1] - ratio)) {
      best = tier;
    }
  }
  return `${best[0]}x${best[1]}`;
}

export function validateArgs(model: string, args: CliArgs): void {
  if (!model.toLowerCase().includes("seedream")) {
    throw new Error(`360 image generation supports only Seedream models; received ${model}.`);
  }
  if (args.n !== 1) {
    throw new Error("360 image generation models support exactly one output image per request.");
  }
  const outputExtension = args.imagePath ? path.extname(args.imagePath).toLowerCase() : "";
  if (outputExtension && outputExtension !== ".png") {
    throw new Error(
      `qihu360 supports only PNG output paths (.png); received ${args.imagePath}. Use .png or omit the extension.`
    );
  }
}

export function buildGenerationsBody(
  prompt: string,
  model: string,
  args: Pick<CliArgs, "aspectRatio" | "size" | "quality" | "imageApiDialect">
): Qihu360GenerationsBody {
  return {
    model,
    prompt,
    extra_body: {
      size: resolveSize(args.aspectRatio, args.size),
      watermark: false,
      output_format: "png",
    },
  };
}

function guessMimeType(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".webp") return "image/webp";
  if (ext === ".gif") return "image/gif";
  if (ext === ".bmp") return "image/bmp";
  if (ext === ".tiff" || ext === ".tif") return "image/tiff";
  return "image/png";
}

async function referenceToDataUri(reference: string): Promise<string> {
  if (/^(https?:|data:)/i.test(reference)) return reference;
  const fullPath = path.resolve(reference);
  const bytes = await readFile(fullPath);
  return `data:${guessMimeType(fullPath)};base64,${bytes.toString("base64")}`;
}

export async function generateImage(
  prompt: string,
  model: string,
  args: CliArgs
): Promise<Uint8Array> {
  const apiKey = getApiKey();
  if (!apiKey) {
    throw new Error(
      "QIHU_API_KEY is required. Set it in your environment or .baoyu-skills/.env file."
    );
  }

  validateArgs(model, args);
  const baseUrl = getBaseUrl();
  const extraBody: Record<string, unknown> = {
    size: resolveSize(args.aspectRatio, args.size),
    watermark: false,
    output_format: "png",
  };
  let endpoint = `${baseUrl}/v1/images/generations`;

  if (args.referenceImages.length > 0) {
    // 参考图（单图字符串 / 多图数组）走 /v1/images/edits 的 extra_body.image。
    const references = await Promise.all(args.referenceImages.map(referenceToDataUri));
    extraBody.image = references.length === 1 ? references[0] : references;
    endpoint = `${baseUrl}/v1/images/edits`;
  }

  const body = { model, prompt, extra_body: extraBody };
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const err = await response.text();
    throw new Error(`360 Image API error (${response.status}): ${err}`);
  }

  const result = (await response.json()) as Qihu360ImageResponse;
  const imageData = await extractImageFromResponse(result);
  assertPngImage(imageData);
  return imageData;
}

const PNG_MAGIC_BYTES = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10]);

function assertPngImage(imageData: Uint8Array): void {
  if (PNG_MAGIC_BYTES.every((byte, index) => imageData[index] === byte)) return;
  if (imageData[0] === 255 && imageData[1] === 216 && imageData[2] === 255) {
    throw new Error(
      "Unexpected JPEG data; expected PNG magic bytes from 360 Image API after requesting output_format=png."
    );
  }
  throw new Error(
    "Unexpected non-PNG data; expected PNG magic bytes from 360 Image API after requesting output_format=png."
  );
}

// Cap how long a CDN image download may hang. undici's default headers/body
// timeout is ~5 min; the company-server egress bug (PMTU black hole) made
// downloads hang that long before failing. 30s is a generous ceiling for a
// ~2 MB image and keeps the agent from stalling on a slow/dead CDN.
const DOWNLOAD_TIMEOUT_MS = 30_000;

function describeFetchCause(e: unknown): string {
  if (e instanceof Error) {
    const cause = (e as { cause?: { code?: string; message?: string } }).cause;
    if (cause?.code) return cause.code;
    if (cause?.message) return cause.message;
    return e.name; // e.g. HeadersTimeoutError / BodyTimeoutError on abort
  }
  return "unknown";
}

function wrapDownloadError(url: string, e: unknown): Error {
  const message = e instanceof Error ? e.message : String(e);
  return new Error(`Download failed for ${url}: ${message} (cause: ${describeFetchCause(e)})`);
}

export async function extractImageFromResponse(result: Qihu360ImageResponse): Promise<Uint8Array> {
  const img = result.data?.[0];

  if (img?.b64_json && !img.url) {
    return Uint8Array.from(Buffer.from(img.b64_json, "base64"));
  }

  if (img?.url) {
    try {
      let imgRes: Response;
      try {
        imgRes = await fetch(img.url, { signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS) });
      } catch (e) {
        throw wrapDownloadError(img.url, e);
      }
      if (!imgRes.ok) {
        throw new Error(`Download ${img.url} returned HTTP ${imgRes.status} ${imgRes.statusText}`);
      }
      // Body read can still fail mid-stream (reset/timeout) after a 200 — wrap it
      // so the URL + cause are surfaced instead of a bare Node fetch error.
      try {
        return new Uint8Array(await imgRes.arrayBuffer());
      } catch (e) {
        throw wrapDownloadError(img.url, e);
      }
    } catch (error) {
      if (img.b64_json) {
        return Uint8Array.from(Buffer.from(img.b64_json, "base64"));
      }
      throw error;
    }
  }

  throw new Error("No image in 360 API response");
}

export function getDefaultOutputExtension(): ".png" {
  return ".png";
}
