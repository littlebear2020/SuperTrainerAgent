import type { CardData, CardOutputFormat, CardTheme, CardOutput } from './card';

export type SkillConfig = {
  defaultTheme: CardTheme;
  defaultFormat: CardOutputFormat;
  outputDir: string;
  cardWidth: number;
  cardHeight: number;
  videoFps: number;
  videoDurationSeconds: number;
  concurrency: number;
};

export type GenerateOptions = {
  cards: CardData[];
  format?: CardOutputFormat;
  theme?: CardTheme;
  outputDir?: string;
  onProgress?: (progress: BatchProgress) => void;
};

export type BatchProgress = {
  total: number;
  completed: number;
  current: string;
  phase: 'svg' | 'video';
  percent: number;
};

export type BatchResult = {
  outputs: CardOutput[];
  totalSucceeded: number;
  totalFailed: number;
  durationMs: number;
};
