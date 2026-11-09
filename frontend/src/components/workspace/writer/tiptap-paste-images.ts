/**
 * Image paste / drop support for the Writer editor (issue ①).
 *
 * TipTap's stock editor only inserts images by URL. These handlers let the
 * user paste a local image (or screenshot) or drag a file in: the image is
 * uploaded via the writer assets endpoint and inserted as a CustomImage node.
 * Non-image content falls through to TipTap's default paste/drop handling
 * (handlers return false).
 */

import { TextSelection } from "@tiptap/pm/state";
import { type EditorView } from "@tiptap/pm/view";

/** Type guard: a non-null file whose MIME type is an image. */
function isImageFile(file: File | null): file is File {
  return file?.type.startsWith("image/") === true;
}

/**
 * Extract image files (only) from a paste/drop DataTransfer. Non-image files
 * (PDFs, text, …) are skipped so they fall through to normal paste/drop.
 */
export function extractImageFiles(dataTransfer: DataTransfer | null): File[] {
  if (!dataTransfer) return [];
  const files: File[] = [];
  const items = dataTransfer.items;
  if (items) {
    for (const item of Array.from(items)) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (isImageFile(file)) files.push(file);
      }
    }
  }
  // Fallback when items[] isn't populated (some browsers only fill .files).
  if (files.length === 0 && dataTransfer.files) {
    for (const file of Array.from(dataTransfer.files)) {
      if (isImageFile(file)) files.push(file);
    }
  }
  return files;
}

export type ImageUploadFn = (file: File) => Promise<{
  url: string;
  width?: number | null;
  height?: number | null;
}>;

/** Upload each image and insert it as an image node at the current selection. */
async function insertUploadedImages(
  view: EditorView,
  files: File[],
  upload: ImageUploadFn,
  onError?: (file: File) => void,
): Promise<void> {
  for (const file of files) {
    try {
      const result = await upload(file);
      const imageType = view.state.schema.nodes.image;
      if (!imageType) continue;
      const node = imageType.create({
        src: result.url,
        width: result.width ?? null,
        height: result.height ?? null,
      });
      view.dispatch(view.state.tr.replaceSelectionWith(node));
    } catch {
      // Per-image resilience: a failed upload shouldn't abort the rest —
      // but it MUST be surfaced (P1-11), not silently dropped.
      onError?.(file);
    }
  }
}

/**
 * ProseMirror editorProps handlers that upload pasted/dropped images and
 * insert them. Returns `{ handlePaste, handleDrop }` ready to spread into
 * `useEditor({ editorProps })`.
 */
export function createImagePasteDropHandlers(
  upload: ImageUploadFn,
  onError?: (file: File) => void,
): {
  handlePaste: (view: EditorView, event: ClipboardEvent) => boolean;
  handleDrop: (view: EditorView, event: DragEvent) => boolean;
} {
  return {
    handlePaste: (view, event) => {
      const files = extractImageFiles(event.clipboardData);
      if (files.length === 0) return false;
      event.preventDefault();
      void insertUploadedImages(view, files, upload, onError);
      return true;
    },
    handleDrop: (view, event) => {
      const files = extractImageFiles(event.dataTransfer);
      if (files.length === 0) return false;
      event.preventDefault();
      // Move the selection to the drop point so images land where dropped.
      const coords = view.posAtCoords({
        left: event.clientX,
        top: event.clientY,
      });
      if (coords) {
        const sel = TextSelection.near(view.state.doc.resolve(coords.pos));
        view.dispatch(view.state.tr.setSelection(sel));
      }
      void insertUploadedImages(view, files, upload, onError);
      return true;
    },
  };
}
