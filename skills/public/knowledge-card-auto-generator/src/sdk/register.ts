import type { SkillConfig } from '../types/sdk';
import type { CardTheme, CardOutputFormat } from '../types/card';

export const DEFAULT_CONFIG: SkillConfig = {
  defaultTheme: 'tech-blue',
  defaultFormat: 'both',
  outputDir: './output',
  cardWidth: 1920,
  cardHeight: 1080,
  videoFps: 30,
  videoDurationSeconds: 5,
  concurrency: 2,
};

export function register(overrides?: Partial<SkillConfig>): SkillConfig {
  return { ...DEFAULT_CONFIG, ...overrides };
}
