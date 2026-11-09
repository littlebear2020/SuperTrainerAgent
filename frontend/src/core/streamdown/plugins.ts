import { code } from "@streamdown/code";
import { mermaid } from "@streamdown/mermaid";
import GithubSlugger from "github-slugger";
import type { Element, Nodes, Root } from "hast";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, {
  defaultSchema,
  type Options as SanitizeOptions,
} from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { StreamdownProps } from "streamdown";
import { visit } from "unist-util-visit";

// （PR-2 3.3：骨架自上游 DeerFlow plugins.ts 移植；schema 按 D1 合流本地
// SEC-H1——本地口径为准（放行 "*" className 与 span[data-rough-notation]），
// 叠加上游为「KaTeX 在 sanitize 之后运行」所需的 math class 白名单与 tel 协议。）
const katexOptions = {
  output: "html",
  throwOnError: false,
  strict: false,
} as const;

type RehypePlugin = NonNullable<StreamdownProps["rehypePlugins"]>[number];

/**
 * Schema for the rehype-sanitize step that every custom rehype chain below
 * re-applies. Single source of truth for raw-HTML rendering paths: this file
 * and the writer preview (components/workspace/writer/markdown-preview.tsx)
 * share it (SEC-H1, 导出名 `streamdownSanitizeSchema` 不变).
 *
 * Why an explicit sanitize step is needed at all: streamdown@2.5 swaps its
 * whole default rehype chain `[rehype-raw, rehype-sanitize, rehype-harden]`
 * for the caller's array as soon as a `rehypePlugins` prop is passed. Any
 * custom chain therefore silently loses sanitization unless it re-adds one.
 *
 * The schema starts from rehype-sanitize's GitHub-style `defaultSchema`
 * (the same base streamdown's built-in sanitize step uses): it keeps the
 * legitimate HTML that LLM/authored markdown documents may embed — tables,
 * `<details>`, images, alignment/size attributes, … — while dropping
 * `<script>`, `<iframe>`, `<style>`, `on*` event handlers and non-allow-listed
 * URL schemes such as `javascript:` (`href` is limited to http(s)/mailto/tel
 * and relative references).
 *
 * ── 本地 SEC-H1 扩展（D1：以本地为准，上游不加）──────────────────────
 * - `"*"` 放行 `className`：作用对象是 raw HTML 透传（用户手写/粘贴的 class
 *   会保留）——class 不是 XSS 向量（不能执行 JS），可接受。注意 writer 评审
 *   问题 3 + 批次B评审跟进的原结论随导出名一并继承。
 * - `span` 放行 `data-rough-notation`（kebab 与 camelCase 两种形态）：存量
 *   文档可能携带 D-3 已移除的自动标注 span——属性本身无害，保留透传让它们
 *   降级为带 data 属性的普通 span。
 *
 * ── 上游扩展（D1 合流带入，为「KaTeX 在 sanitize 之后运行」所必需）────
 * - `tel:` hrefs — mirrors streamdown's built-in schema.
 * - `math-inline` / `math-display` values for `className` on `code` —
 *   remark-math marks math spans as `<code class="language-math
 *   math-inline|math-display">` and rehype-katex (which runs *after* the
 *   sanitize step, see `rehypeSanitizeStep`) detects math through exactly
 *   those classes. Without this entry sanitize strips the markers and math
 *   stops rendering. hast-util-sanitize only honors the first definition
 *   per property name, so the default `^language-.` allow-list is widened
 *   in place rather than appended to.
 * - `metastring` on `code` — parity with streamdown's built-in schema.
 *
 * Deliberately NOT extended with the `style` attribute/tag, `iframe`, or
 * other arbitrary tag names.
 */
export const streamdownSanitizeSchema: SanitizeOptions = {
  ...defaultSchema,
  protocols: {
    ...defaultSchema.protocols,
    href: [...(defaultSchema.protocols?.href ?? []), "tel"],
  },
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] ?? []), "className"],
    span: [
      ...(defaultSchema.attributes?.span ?? []),
      "data-rough-notation",
      "dataRoughNotation",
      "className",
    ],
    code: [
      ["className", /^language-./, "math-inline", "math-display"],
      "metastring",
    ],
  },
};

/**
 * The sanitize entry re-inserted into every custom rehype plugin chain.
 *
 * Ordering constraints (streamdown's own default chain has the same shape:
 * raw → sanitize, with its math rehype plugin appended after sanitize):
 * - AFTER `rehypeRaw`: raw HTML must first be parsed into hast nodes;
 *   before that it is inert text and cannot be sanitized.
 * - BEFORE `rehypeKatex`: KaTeX emits class/style-heavy trusted markup that
 *   the sanitize schema would strip, breaking math rendering.
 * - `rehypeScopedSlug` also runs after this step so generated heading ids
 *   keep sanitize's `user-content-` clobber prefix (and fragment links are
 *   translated to match).
 */
export const rehypeSanitizeStep = [
  rehypeSanitize,
  streamdownSanitizeSchema,
] as RehypePlugin;

/** The id prefix rehype-sanitize applies to guard against DOM clobbering. */
const CLOBBER_PREFIX = defaultSchema.clobberPrefix ?? "user-content-";

function nodeText(node: Nodes): string {
  if (node.type === "text") {
    return node.value;
  }
  if ("children" in node) {
    return node.children.map(nodeText).join("");
  }
  return "";
}

/**
 * Heading-anchor plugin for chains that run AFTER `rehypeSanitizeStep`.
 *
 * rehype-sanitize prefixes `id` attributes (default `user-content-`) so a
 * hostile heading such as `## current` cannot mint an unprefixed
 * `id="current"` — the exact DOM-clobbering shape the sanitizer guards
 * against. A slug plugin running after sanitize must therefore keep that
 * prefix: generated heading ids get `CLOBBER_PREFIX + slug`, and in-page
 * fragment links are translated by `rehypeClobberFragments` (which runs
 * right after sanitize, before this plugin). Headings whose id sanitize
 * already prefixed (raw HTML `<h2 id="x">`) keep that id.
 */
export function rehypeScopedSlug() {
  const slugger = new GithubSlugger();
  return (tree: Root) => {
    // Streamdown caches the unified processor by plugin name, so this
    // attacher-level slugger instance survives across parses. Without a
    // reset, rendering the same heading twice yields `heading` then
    // `heading-1` (rehype-slug resets for the same reason).
    slugger.reset();
    visit(tree, "element", (node: Element) => {
      if (!/^h[1-6]$/.test(node.tagName)) {
        return;
      }
      if (node.properties?.id) {
        return;
      }
      node.properties = {
        ...node.properties,
        id: CLOBBER_PREFIX + slugger.slug(nodeText(node)),
      };
    });
  };
}

/**
 * Keeps fragment navigation consistent with rehype-sanitize's clobber
 * prefix. Runs immediately after `rehypeSanitizeStep` in every chain:
 *
 * - remark-rehype already emits GFM footnote anchors PRE-prefixed
 *   (`id="user-content-fn-1"`, `href="#user-content-fn-1"`), and sanitize
 *   prefixes the id again — producing `user-content-user-content-fn-1`
 *   while the href stays single-prefixed, breaking every footnote.
 *   Ids that ended up double-prefixed are normalized back to one prefix.
 * - Fragment links written without the prefix (`#foo`) can only resolve
 *   against prefixed ids after sanitization, so they are translated to
 *   `#user-content-foo`. Already-prefixed hrefs and external URLs are
 *   untouched.
 */
export function rehypeClobberFragments() {
  return (tree: Root) => {
    const doublePrefix = `${CLOBBER_PREFIX}${CLOBBER_PREFIX}`;
    visit(tree, "element", (node: Element) => {
      const id = node.properties?.id;
      if (typeof id === "string" && id.startsWith(doublePrefix)) {
        node.properties.id = id.slice(CLOBBER_PREFIX.length);
      }
    });
    visit(tree, "element", (node: Element) => {
      if (node.tagName !== "a") {
        return;
      }
      const href = node.properties?.href;
      if (
        typeof href === "string" &&
        href.length > 1 &&
        href.startsWith("#") &&
        !href.startsWith(`#${CLOBBER_PREFIX}`)
      ) {
        node.properties.href = `#${CLOBBER_PREFIX}${href.slice(1)}`;
      }
    });
  };
}

const sharedRemarkPlugins = [
  // D3（2026-10-16 评审）：对齐上游/GFM 标准——单波浪线不再渲染删除线
  // （`~x~` 存量渲染差异随 PR-2 changelog 知会）。
  [remarkGfm, { singleTilde: false }],
  [remarkMath, { singleDollarTextMath: true }],
] as StreamdownProps["remarkPlugins"];

export const streamdownRenderingPlugins = {
  code,
  mermaid,
} satisfies NonNullable<StreamdownProps["plugins"]>;

export const streamdownPlugins = {
  plugins: streamdownRenderingPlugins,
  remarkPlugins: sharedRemarkPlugins,
  // Passing rehypePlugins to streamdown drops its default sanitize chain,
  // so every chain built from this preset carries rehypeSanitizeStep after
  // rehypeRaw and before rehypeKatex (see rehypeSanitizeStep for why the
  // order matters).
  rehypePlugins: [
    rehypeRaw,
    rehypeSanitizeStep,
    rehypeClobberFragments,
    [rehypeKatex, katexOptions],
  ] as StreamdownProps["rehypePlugins"],
};

export const streamdownWordAnimation = {
  animation: "fadeIn",
  duration: 200,
  sep: "word",
} as const satisfies Exclude<StreamdownProps["animated"], boolean | undefined>;

export const streamdownSmoothStreamingAnimation = {
  ...streamdownWordAnimation,
  // Streamdown defaults to 40ms per new word. A large chunk can therefore
  // delay later list-item text by seconds while its native marker is already
  // visible. Smooth content reveal owns the pacing here, so start every new
  // word's fade together with its surrounding marker.
  stagger: 0,
} as const satisfies Exclude<StreamdownProps["animated"], boolean | undefined>;

/**
 * Keeps native list markers in step with Streamdown's word reveal.
 *
 * A trailing `2.` or `-` is parsed as an empty list item while content is
 * streaming. Hide that transient item, then mark it for a matching marker
 * animation as soon as its first child arrives. Keep mid-list empty items in
 * the box tree so ordered-list counters never renumber later items.
 */
export function rehypeStreamingListItems() {
  return (tree: Root) => {
    visit(tree, "element", (node, index, parent) => {
      if (node.tagName !== "li") {
        return;
      }

      if (node.children.length === 0) {
        const isTrailingListItem =
          index !== undefined &&
          parent?.type === "element" &&
          (parent.tagName === "ol" || parent.tagName === "ul") &&
          !parent.children
            .slice(index + 1)
            .some(
              (sibling) =>
                sibling.type === "element" && sibling.tagName === "li",
            );
        if (isTrailingListItem) {
          node.properties.hidden = true;
        }
        return;
      }

      node.properties["data-streaming-list-item"] = "true";
    });
  };
}

// Same chain minus rehypeRaw, so raw HTML stays inert text; the sanitize
// step survives the filter and still cleans autolink URLs etc.
export const streamdownPluginsWithoutRawHtml = {
  plugins: streamdownPlugins.plugins,
  remarkPlugins: streamdownPlugins.remarkPlugins,
  rehypePlugins: streamdownPlugins.rehypePlugins?.filter(
    (p) => p !== rehypeRaw,
  ) as StreamdownProps["rehypePlugins"],
};

// Plugins for reasoning/thinking content — derived from streamdownPlugins but without rehypeRaw,
// to prevent LLM-hallucinated HTML tags (e.g. <simd>) from being rendered as DOM elements.
export const reasoningPlugins = streamdownPluginsWithoutRawHtml;
