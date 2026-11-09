"use client";

/**
 * CustomImage — a resize-enabled image node for the Writer editor (issue ②).
 *
 * TipTap v3's stock `@tiptap/extension-image` has no resize affordance. This
 * extension extends it with:
 *   - `width` / `height` node attributes parsed from `<img loading="lazy" width height>` (and
 *     inline style) and rendered back onto the element;
 *   - a React NodeView that paints a corner drag-handle to resize the image
 *     while preserving aspect ratio (`clampImageWidth` enforces sane bounds);
 *   - a tiptap-markdown serializer that emits `<img loading="lazy" … width height />` so the
 *     chosen size survives the markdown round-trip — the doc store is markdown
 *     `content_json`, so a size lost in serialization reverts on reload.
 */

import { Image } from "@tiptap/extension-image";
import {
  type NodeViewProps,
  NodeViewWrapper,
  ReactNodeViewRenderer,
} from "@tiptap/react";
import {
  useCallback,
  useRef,
  type CSSProperties,
  type PointerEvent,
  type ReactElement,
} from "react";

export const MIN_IMAGE_WIDTH = 40;
export const MAX_IMAGE_WIDTH = 1200;

/**
 * Clamp an image width to sane bounds (used by the resize handle). NaN /
 * Infinity fall back to the minimum so a corrupt attr never collapses the
 * image to zero or stretches it across the page.
 */
export function clampImageWidth(width: number): number {
  if (!Number.isFinite(width)) return MIN_IMAGE_WIDTH;
  return Math.max(
    MIN_IMAGE_WIDTH,
    Math.min(MAX_IMAGE_WIDTH, Math.round(width)),
  );
}

/** Escape a value for safe interpolation into an HTML attribute. */
function escapeAttr(value: string | null | undefined): string {
  return (value ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

interface ImageAttrs {
  src: string;
  alt?: string | null;
  width?: number | null;
  height?: number | null;
}

function ImageView({
  node,
  updateAttributes,
  selected,
}: NodeViewProps): ReactElement {
  const { src, alt, width, height } = node.attrs as ImageAttrs;
  const dragRef = useRef<{
    startX: number;
    startW: number;
    aspect: number;
  } | null>(null);

  const onHandlePointerDown = useCallback(
    (e: PointerEvent<HTMLSpanElement>) => {
      e.preventDefault();
      e.stopPropagation();
      const baseW = width ?? dragRef.current?.startW ?? 300;
      const aspect = height && width ? width / height : 0;
      dragRef.current = { startX: e.clientX, startW: baseW, aspect };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);

      const onMove = (ev: globalThis.PointerEvent) => {
        const drag = dragRef.current;
        if (!drag) return;
        const nextW = clampImageWidth(drag.startW + (ev.clientX - drag.startX));
        const nextH = drag.aspect > 0 ? Math.round(nextW / drag.aspect) : null;
        updateAttributes({ width: nextW, height: nextH });
      };
      const onUp = () => {
        dragRef.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [width, height, updateAttributes],
  );

  const style: CSSProperties =
    width != null
      ? {
          width: `${width}px`,
          ...(height != null ? { height: `${height}px` } : {}),
        }
      : {};

  return (
    <NodeViewWrapper
      className="custom-image-wrapper"
      data-selected={selected || undefined}
    >
      <img
        loading="lazy"
        src={src}
        alt={alt ?? ""}
        width={width ?? undefined}
        height={height ?? undefined}
        style={style}
        draggable={false}
      />
      <span
        className="custom-image-resize-handle"
        contentEditable={false}
        onPointerDown={onHandlePointerDown}
        role="button"
        tabIndex={-1}
        aria-label="拖拽调整图片大小"
      />
    </NodeViewWrapper>
  );
}

export const CustomImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      src: {
        default: null,
        parseHTML: (el) => el.getAttribute("src") ?? "",
      },
      alt: {
        default: "",
        parseHTML: (el) => el.getAttribute("alt") ?? "",
      },
      width: {
        default: null,
        parseHTML: (el) => {
          const raw = el.getAttribute("width") ?? el.style.width ?? "";
          const n = Number.parseInt(raw, 10);
          return Number.isFinite(n) && n > 0 ? n : null;
        },
        renderHTML: (attrs) => (attrs.width ? { width: attrs.width } : {}),
      },
      height: {
        default: null,
        parseHTML: (el) => {
          const raw = el.getAttribute("height") ?? el.style.height ?? "";
          const n = Number.parseInt(raw, 10);
          return Number.isFinite(n) && n > 0 ? n : null;
        },
        renderHTML: (attrs) => (attrs.height ? { height: attrs.height } : {}),
      },
    };
  },

  addNodeView() {
    return ReactNodeViewRenderer(ImageView);
  },

  addStorage() {
    return {
      markdown: {
        // Emit an <img loading="lazy"> carrying width/height so the chosen size survives the
        // markdown round-trip (parsed back via the width/height parseHTML
        // above). tiptap-markdown's default `![alt](src)` drops width.
        serialize(
          state: {
            write: (s: string) => void;
            closeBlock: (n: unknown) => void;
          },
          node: {
            attrs: {
              src?: string | null;
              alt?: string | null;
              width?: number | null;
              height?: number | null;
            };
          },
        ) {
          const src = escapeAttr(node.attrs.src);
          const alt = node.attrs.alt
            ? ` alt="${escapeAttr(node.attrs.alt)}"`
            : "";
          const w = node.attrs.width ? ` width="${node.attrs.width}"` : "";
          const h = node.attrs.height ? ` height="${node.attrs.height}"` : "";
          state.write(`<img loading="lazy" src="${src}"${alt}${w}${h} />`);
          state.closeBlock(node);
        },
      },
    };
  },
});
