/**
 * Wraps text into lines that fit within a given SVG width constraint.
 * Uses character-width approximation since Node.js cannot measure text natively.
 */
export function wrapSvgText(
  text: string,
  maxWidth: number,
  fontSize: number,
  fontWeight: 'normal' | 'bold' = 'normal',
): string[] {
  const charWidth = fontSize * (fontWeight === 'bold' ? 0.65 : 0.58);
  const maxCharsPerLine = Math.floor(maxWidth / charWidth);

  if (maxCharsPerLine <= 0) {
    return [text];
  }

  const words = text.split(' ');
  const lines: string[] = [];
  let currentLine = '';

  for (const word of words) {
    const testLine = currentLine ? `${currentLine} ${word}` : word;
    if (testLine.length > maxCharsPerLine && currentLine) {
      lines.push(currentLine);
      currentLine = word;
    } else {
      currentLine = testLine;
    }
  }
  if (currentLine) {
    lines.push(currentLine);
  }

  return lines;
}

/**
 * Estimates the total height of a text block given font size, line height,
 * and the wrapped lines.
 */
export function estimateTextHeight(
  lines: string[],
  fontSize: number,
  lineHeight: number = 1.5,
): number {
  return lines.length * fontSize * lineHeight;
}
