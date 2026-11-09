/**
 * CustomImage resize + round-trip tests (issue ②).
 *
 * The 图片 feature needs Word-like drag-to-resize, and the chosen width must
 * survive a markdown round-trip — the doc store is markdown `content_json`, so
 * a width lost in serialization = broken image size on reload. These drive a
 * real TipTap Editor (happy-dom) to assert width/height persist through
 * getMarkdown / setContent, plus the resize clamp bounds.
 */

import { Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "tiptap-markdown";
import { expect, test } from "vitest";

import {
  MAX_IMAGE_WIDTH,
  MIN_IMAGE_WIDTH,
  CustomImage,
  clampImageWidth,
} from "@/components/workspace/writer/tiptap-image";

function makeEditor(): Editor {
  return new Editor({
    extensions: [StarterKit, CustomImage, Markdown.configure({ html: true })],
    content: "",
  });
}

type MarkdownStorage = { markdown: { getMarkdown: () => string } };

test("image width/height survive a markdown round-trip", () => {
  const editor = makeEditor();
  editor.commands.setContent(
    '<img src="https://example.com/a.jpg" alt="cover" width="320" height="240" />',
  );

  const md = (
    editor.storage as unknown as MarkdownStorage
  ).markdown.getMarkdown();
  // Width must be serialized (as an <img> carrying width — html:true).
  expect(md).toContain("<img");
  expect(md).toMatch(/width=["']?320/);
  expect(md).toMatch(/height=["']?240/);

  // Round-trip: feeding the markdown back preserves the width.
  editor.commands.setContent(md);
  const md2 = (
    editor.storage as unknown as MarkdownStorage
  ).markdown.getMarkdown();
  expect(md2).toMatch(/width=["']?320/);

  editor.destroy();
});

test("clampImageWidth enforces min/max bounds", () => {
  expect(clampImageWidth(10)).toBe(MIN_IMAGE_WIDTH);
  expect(clampImageWidth(5000)).toBe(MAX_IMAGE_WIDTH);
  expect(clampImageWidth(300)).toBe(300);
  expect(clampImageWidth(NaN)).toBe(MIN_IMAGE_WIDTH);
});
