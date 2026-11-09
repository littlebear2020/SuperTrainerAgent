import { bundle } from '@remotion/bundler';
import { renderMedia, selectComposition } from '@remotion/renderer';
import type { CardData } from '../types/card';
import * as fs from 'node:fs';
import * as path from 'node:path';

export async function generateVideo(
  cardData: CardData,
  outputDir: string,
  bundleLocation: string,
  concurrency: number = 2,
): Promise<string> {
  const mp4Dir = path.join(outputDir, 'mp4');
  fs.mkdirSync(mp4Dir, { recursive: true });

  const outputPath = path.join(mp4Dir, `${cardData.id}.mp4`);

  const composition = await selectComposition({
    serveUrl: bundleLocation,
    id: 'KnowledgeCardVideo',
    inputProps: { cardData },
  });

  await renderMedia({
    composition,
    serveUrl: bundleLocation,
    codec: 'h264',
    outputLocation: outputPath,
    inputProps: { cardData },
    concurrency,
    onProgress: ({ progress }) => {
      process.stdout.write(
        `\r  [${cardData.id}] Rendering: ${(progress * 100).toFixed(1)}%`,
      );
    },
  });
  process.stdout.write('\n');

  return outputPath;
}

export async function generatePortraitVideo(
  outputDir: string,
  bundleLocation: string,
  concurrency: number = 2,
): Promise<string> {
  const mp4Dir = path.join(outputDir, 'mp4');
  fs.mkdirSync(mp4Dir, { recursive: true });

  const outputPath = path.join(mp4Dir, 'openclaw-portrait.mp4');

  const composition = await selectComposition({
    serveUrl: bundleLocation,
    id: 'OpenClawPortraitVideo',
    inputProps: {},
  });

  await renderMedia({
    composition,
    serveUrl: bundleLocation,
    codec: 'h264',
    outputLocation: outputPath,
    inputProps: {},
    concurrency,
    onProgress: ({ progress }) => {
      process.stdout.write(
        `\r  [portrait] Rendering: ${(progress * 100).toFixed(1)}%`,
      );
    },
  });
  process.stdout.write('\n');

  return outputPath;
}

export async function createBundle(): Promise<string> {
  const entryPoint = path.resolve(process.cwd(), 'src/Root.tsx');
  const bundleLocation = await bundle({
    entryPoint,
  });
  return bundleLocation;
}
