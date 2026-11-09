import path from "node:path";
import { readFile } from "node:fs/promises";
import type { CliArgs, OpenAIImageApiDialect } from "../types";

const DEFAULT_MODEL = "google/gemini-3.1-flash-image-preview";

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

function parseAspectRatio(ar: string): { width: number; height: number } | null {
  const match = ar.match(/^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/);
  if (!match) return null;
  const w = parseFloat(match[1]!);
  const h = parseFloat(match[2]!);
  if (w <= 0 || h <= 0) return null;
  return { width: w, height: h };
}

function getSize(model: string, ar: string | null, quality: CliArgs["quality"]): string {
  const parsed = ar ? parseAspectRatio(ar) : null;
  const ratio = parsed ? parsed.width / parsed.height : 1;

  if (!parsed || Math.abs(ratio - 1) < 0.1) {
    const edge = quality === "2k" ? 2048 : 1024;
    return `${edge}x${edge}`;
  }

  const targetLongEdge = quality === "2k" ? 2048 : 1024;
  let width: number;
  let height: number;

  if (ratio > 1) {
    width = targetLongEdge;
    height = Math.round(width / ratio);
  } else {
    height = targetLongEdge;
    width = Math.round(height * ratio);
  }

  return `${width}x${height}`;
}

export function validateArgs(model: string, args: CliArgs): void {
  if (args.n > 4) {
    throw new Error("360 image generation supports at most 4 images per request.");
  }
}

export function buildGenerationsBody(
  prompt: string,
  model: string,
  args: Pick<CliArgs, "aspectRatio" | "size" | "quality" | "imageApiDialect">
): Qihu360GenerationsBody {
  const body: Qihu360GenerationsBody = {
    model,
    prompt,
    size: args.size || getSize(model, args.aspectRatio, args.quality),
  };

  return body;
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

  const baseUrl = getBaseUrl();
  const body = buildGenerationsBody(prompt, model, args);

  if (args.n > 1) {
    body.n = args.n;
  }

  const response = await fetch(`${baseUrl}/v1/images/generations`, {
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
  return extractImageFromResponse(result);
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

  if (img?.b64_json) {
    return Uint8Array.from(Buffer.from(img.b64_json, "base64"));
  }

  if (img?.url) {
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
  }

  throw new Error("No image in 360 API response");
}

export function getDefaultOutputExtension(): ".png" {
  return ".png";
}
