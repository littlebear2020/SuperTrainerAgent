/**
 * Split text into segments by sentence-ending punctuation and paragraph breaks.
 * Designed for the TTS segment editor UI — each segment is independently editable.
 */

const SENTENCE_ENDS = new Set("。！？.!?\n");
const HARD_LIMIT = 500;

function splitBySentences(text: string): string[] {
  const result: string[] = [];
  let current = "";
  for (const char of text) {
    current += char;
    if (SENTENCE_ENDS.has(char)) {
      const trimmed = current.trim();
      if (trimmed) result.push(trimmed);
      current = "";
    }
  }
  if (current.trim()) result.push(current.trim());
  return result;
}

function hardSplit(text: string, limit: number): string[] {
  if (text.length <= limit) return [text];
  const result: string[] = [];
  for (let i = 0; i < text.length; i += limit) {
    result.push(text.slice(i, i + limit));
  }
  return result;
}

export function splitTextToSegments(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];

  // Step 1: Split by paragraph breaks
  const paragraphs = trimmed.split(/\n{2,}/);

  const segments: string[] = [];

  for (const para of paragraphs) {
    const paraTrimmed = para.trim();
    if (!paraTrimmed) continue;

    // Step 2: Split by sentence-ending punctuation
    const sentences = splitBySentences(paraTrimmed);

    // Step 3: Hard-split any segment exceeding the limit
    for (const sentence of sentences) {
      if (sentence.length > HARD_LIMIT) {
        segments.push(...hardSplit(sentence, HARD_LIMIT));
      } else {
        segments.push(sentence);
      }
    }
  }

  return segments.length > 0 ? segments : [trimmed];
}
