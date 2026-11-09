/**
 * Brand design templates + helpers (W20).
 *
 * Preset HTML templates for the brand design tab. The templates are STATIC +
 * TRUSTED (not AI-generated) — the AI only fills copy + color variables
 * (structured JSON via `streamAssist`), and the frontend renders locally:
 * trusted template + HTML-escaped vars → NO `sanitizeHtml` needed.
 *
 * Spec: docs/superpowers/plans/2026-07-10-writer-focus-refactor-plan.md §8.4.
 */

// ── Types ──────────────────────────────────────────────────────────────────

export type BrandType = "cover" | "social" | "chart";

export interface BrandColors {
  primary: string;
  secondary: string;
  accent: string;
  bg: string;
}

export interface BrandVars {
  title: string;
  subtitle: string;
  points: string[];
  footer: string;
  colors: Partial<BrandColors>;
}

// ── Preset HTML templates (static, trusted) ────────────────────────────────

/**
 * Cover poster — full-bleed gradient with big title, subtitle, bullet points,
 * and footer. Designed for article cover images.
 */
const COVER_TEMPLATE = `<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 48px 40px; border-radius: 16px; background: linear-gradient(135deg, {{bg}} 0%, {{primary}} 100%); color: {{secondary}};">
<h1 style="font-size: 32px; font-weight: 700; margin: 0 0 12px 0; line-height: 1.3; color: {{accent}};">{{title}}</h1>
<p style="font-size: 16px; margin: 0 0 24px 0; opacity: 0.85;">{{subtitle}}</p>
<ul style="font-size: 14px; line-height: 1.8; margin: 0 0 24px 0; padding-left: 20px; opacity: 0.9;">{{points}}</ul>
<p style="font-size: 12px; margin: 0; opacity: 0.6;">{{footer}}</p>
</div>`;

/**
 * Social card — compact bordered card with title, subtitle, points, footer.
 * Designed for social media share images.
 */
const SOCIAL_TEMPLATE = `<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px; border-radius: 12px; background: {{bg}}; border-left: 4px solid {{primary}};">
<h2 style="font-size: 22px; font-weight: 700; margin: 0 0 8px 0; color: {{primary}};">{{title}}</h2>
<p style="font-size: 14px; margin: 0 0 16px 0; color: {{secondary}};">{{subtitle}}</p>
<ul style="font-size: 13px; line-height: 1.7; margin: 0 0 16px 0; padding-left: 18px; color: {{secondary}};">{{points}}</ul>
<p style="font-size: 11px; margin: 0; color: {{accent}};">{{footer}}</p>
</div>`;

/**
 * Chart/data card — data summary with left-bordered rows. Designed for
 * data-driven summary cards.
 */
const CHART_TEMPLATE = `<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px; border-radius: 12px; background: {{bg}}; border: 1px solid {{primary}}33;">
<h2 style="font-size: 20px; font-weight: 700; margin: 0 0 12px 0; color: {{primary}};">{{title}}</h2>
<p style="font-size: 13px; margin: 0 0 20px 0; color: {{secondary}};">{{subtitle}}</p>
<div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px;">{{points}}</div>
<p style="font-size: 11px; margin: 0; color: {{accent}};">{{footer}}</p>
</div>`;

const BRAND_TEMPLATES: Record<BrandType, string> = {
  cover: COVER_TEMPLATE,
  social: SOCIAL_TEMPLATE,
  chart: CHART_TEMPLATE,
};

// ── Helpers ────────────────────────────────────────────────────────────────

/** HTML-escape a text string for safe interpolation into HTML. */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Safe subset of CSS named colors allowed in color vars. */
const SAFE_NAMED_COLORS = new Set([
  "red",
  "blue",
  "green",
  "black",
  "white",
  "gray",
  "grey",
  "orange",
  "yellow",
  "purple",
  "pink",
  "brown",
  "navy",
  "teal",
  "lime",
  "cyan",
  "magenta",
  "maroon",
  "olive",
  "silver",
  "aqua",
  "fuchsia",
  "indigo",
  "violet",
  "gold",
  "coral",
  "salmon",
  "transparent",
]);

/**
 * Validate a color value — allow hex (#rgb / #rrggbb) or a safe subset of
 * CSS named colors. Falls back to `fallback` for anything else (prevents
 * CSS injection via crafted color strings).
 */
function sanitizeColor(color: string | undefined, fallback: string): string {
  if (!color || typeof color !== "string") return fallback;
  const trimmed = color.trim();
  if (/^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$/.test(trimmed)) return trimmed;
  if (SAFE_NAMED_COLORS.has(trimmed.toLowerCase())) return trimmed;
  return fallback;
}

// Default color palette (modern, high-contrast)
const DEFAULT_COLORS: BrandColors = {
  primary: "#1a1a2e",
  secondary: "#6b7280",
  accent: "#e94560",
  bg: "#f8f9fa",
};

/**
 * Parse `BrandVars` JSON from the raw streamed text. Tolerant of markdown
 * code fences and surrounding prose — extracts the first `{...}` JSON object
 * and normalizes fields. Returns `null` if no valid JSON or no useful content.
 */
export function parseBrandVars(rawText: string): BrandVars | null {
  if (!rawText || typeof rawText !== "string") return null;

  let text = rawText.trim();
  if (!text) return null;

  // Strip markdown code fences (```json ... ``` or ``` ... ```)
  const fenceMatch = /```(?:json)?\s*\n?([\s\S]*?)```/.exec(text);
  if (fenceMatch?.[1]) {
    text = fenceMatch[1].trim();
  }

  // Find the first JSON object in the text
  const jsonMatch = /\{[\s\S]*\}/.exec(text);
  if (!jsonMatch) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonMatch[0]);
  } catch {
    return null;
  }

  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return null;
  }

  const obj = parsed as Record<string, unknown>;

  const title = typeof obj.title === "string" ? obj.title : "";
  const subtitle = typeof obj.subtitle === "string" ? obj.subtitle : "";
  const points = Array.isArray(obj.points)
    ? obj.points.filter((p): p is string => typeof p === "string")
    : [];
  const footer = typeof obj.footer === "string" ? obj.footer : "";

  // Extract color strings
  const rawColors =
    typeof obj.colors === "object" &&
    obj.colors !== null &&
    !Array.isArray(obj.colors)
      ? (obj.colors as Record<string, unknown>)
      : {};
  const colors: Partial<BrandColors> = {};
  if (typeof rawColors.primary === "string") colors.primary = rawColors.primary;
  if (typeof rawColors.secondary === "string")
    colors.secondary = rawColors.secondary;
  if (typeof rawColors.accent === "string") colors.accent = rawColors.accent;
  if (typeof rawColors.bg === "string") colors.bg = rawColors.bg;

  // Must have at least some useful content
  if (!title && !subtitle && points.length === 0 && !footer) {
    return null;
  }

  return { title, subtitle, points, footer, colors };
}

/**
 * Render a brand template: pick the preset for `type`, HTML-escape all text
 * vars (prevents injection — this is why `sanitizeHtml` is unneeded), fill
 * placeholders, and return the final HTML string.
 *
 * Uses a single-pass regex replacement so interpolated values are never
 * re-scanned for placeholders (e.g. a point containing `{{title}}` is safe).
 */
export function renderBrandTemplate(type: BrandType, vars: BrandVars): string {
  const template = BRAND_TEMPLATES[type] ?? BRAND_TEMPLATES.cover;

  // Sanitize colors (fall back to defaults)
  const primary = sanitizeColor(vars.colors?.primary, DEFAULT_COLORS.primary);
  const secondary = sanitizeColor(
    vars.colors?.secondary,
    DEFAULT_COLORS.secondary,
  );
  const accent = sanitizeColor(vars.colors?.accent, DEFAULT_COLORS.accent);
  const bg = sanitizeColor(vars.colors?.bg, DEFAULT_COLORS.bg);

  // Escape text vars
  const title = escapeHtml(vars.title ?? "");
  const subtitle = escapeHtml(vars.subtitle ?? "");
  const footer = escapeHtml(vars.footer ?? "");

  // Build points HTML based on template type
  const points = vars.points ?? [];
  let pointsHtml: string;
  if (type === "chart") {
    // Chart: left-bordered rows
    pointsHtml = points
      .map(
        (p) =>
          `<div style="padding: 8px 12px; border-left: 3px solid ${primary}; font-size: 13px; color: ${secondary};">${escapeHtml(p)}</div>`,
      )
      .join("");
  } else {
    // Cover + social: list items
    pointsHtml = points
      .map((p) => `<li style="margin-bottom: 4px;">${escapeHtml(p)}</li>`)
      .join("");
  }

  // Single-pass replacement — no value is re-scanned for placeholders
  const values: Record<string, string> = {
    title,
    subtitle,
    footer,
    points: pointsHtml,
    primary,
    secondary,
    accent,
    bg,
  };
  return template.replace(/\{\{(\w+)\}\}/g, (_, key: string) => {
    return values[key] ?? "";
  });
}
