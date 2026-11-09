import { generateSvg } from './svg-generator';
import { generateVideo, createBundle } from './video-generator';
import type { CardData, CardOutput, CardOutputFormat } from '../types/card';
import type { BatchProgress, BatchResult } from '../types/sdk';

export async function generateCards(options: {
  cards: CardData[];
  format: CardOutputFormat;
  outputDir: string;
  concurrency?: number;
  onProgress?: (progress: BatchProgress) => void;
}): Promise<BatchResult> {
  const startTime = Date.now();
  const outputs: CardOutput[] = [];
  let totalSucceeded = 0;
  let totalFailed = 0;

  // Pre-bundle for video rendering (only if MP4 is needed)
  let bundleLocation: string | undefined;
  if (options.format === 'mp4' || options.format === 'both') {
    console.log('Bundling Remotion project...');
    bundleLocation = await createBundle();
    console.log('Bundle ready.');
  }

  const total = options.cards.length;

  for (let i = 0; i < total; i++) {
    const card = options.cards[i];
    const output: CardOutput = { id: card.id, success: true };

    try {
      // SVG generation
      if (options.format === 'svg' || options.format === 'both') {
        options.onProgress?.({
          total,
          completed: i,
          current: card.id,
          phase: 'svg',
          percent: Math.round(
            ((i * (options.format === 'both' ? 0.3 : 1)) / total) * 100,
          ),
        });
        output.svgPath = await generateSvg(card, options.outputDir);
      }

      // Video generation
      if ((options.format === 'mp4' || options.format === 'both') && bundleLocation) {
        options.onProgress?.({
          total,
          completed: i,
          current: card.id,
          phase: 'video',
          percent: Math.round(((i + 0.5) / total) * 100),
        });
        output.mp4Path = await generateVideo(
          card,
          options.outputDir,
          bundleLocation,
          options.concurrency,
        );
      }

      totalSucceeded++;
    } catch (error) {
      output.success = false;
      output.error = error instanceof Error ? error.message : String(error);
      totalFailed++;
      console.error(`  FAIL [${card.id}]: ${output.error}`);
    }

    outputs.push(output);
  }

  return {
    outputs,
    totalSucceeded,
    totalFailed,
    durationMs: Date.now() - startTime,
  };
}
