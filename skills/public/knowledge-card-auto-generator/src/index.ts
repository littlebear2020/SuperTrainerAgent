import { Command } from 'commander';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { register } from './sdk/register';
import { generateCards } from './sdk/batch-processor';
import type { CardData, CardOutputFormat, CardTheme } from './types/card';

const program = new Command();

program
  .name('knowledge-card-generator')
  .description('Generate knowledge cards (SVG + MP4) from structured data')
  .version('1.0.0')
  .requiredOption('--input <path>', 'Path to JSON input file containing card data array')
  .option('--format <format>', 'Output format: svg, mp4, both', 'both')
  .option(
    '--theme <theme>',
    'Default theme: tech-blue, neon-purple, emerald-green',
    'tech-blue',
  )
  .option('--output <dir>', 'Output directory', './output')
  .option('--concurrency <n>', 'Parallel render limit for video', '2')
  .action(async (options) => {
    const config = register({
      outputDir: path.resolve(options.output),
      defaultFormat: options.format as CardOutputFormat,
      defaultTheme: options.theme as CardTheme,
      concurrency: parseInt(options.concurrency, 10),
    });

    // Read and parse input
    const inputPath = path.resolve(options.input);
    if (!fs.existsSync(inputPath)) {
      console.error(`Error: Input file not found: ${inputPath}`);
      process.exit(1);
    }

    const raw = fs.readFileSync(inputPath, 'utf-8');
    let cards: CardData[];

    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        console.error('Error: Input JSON must be an array of card objects.');
        process.exit(1);
      }
      cards = parsed;
    } catch {
      console.error('Error: Failed to parse input JSON file.');
      process.exit(1);
    }

    // Validate cards
    for (const card of cards) {
      if (!card.id || !card.title || !Array.isArray(card.points)) {
        console.error(
          `Error: Card "${card.id ?? 'unknown'}" is missing required fields (id, title, points).`,
        );
        process.exit(1);
      }
    }

    // Apply default theme to cards that don't specify one
    const cardsWithDefaults = cards.map((card) => ({
      ...card,
      theme: card.theme ?? config.defaultTheme,
    }));

    console.log(`Generating ${cardsWithDefaults.length} card(s)...`);
    console.log(`  Format: ${config.defaultFormat}`);
    console.log(`  Theme:  ${config.defaultTheme}`);
    console.log(`  Output: ${config.outputDir}`);
    console.log();

    const result = await generateCards({
      cards: cardsWithDefaults,
      format: config.defaultFormat,
      outputDir: config.outputDir,
      concurrency: config.concurrency,
      onProgress: (progress) => {
        console.log(
          `[${progress.completed + 1}/${progress.total}] ${progress.phase.toUpperCase()}: ${progress.current} (${progress.percent}%)`,
        );
      },
    });

    console.log();
    console.log(`Done in ${(result.durationMs / 1000).toFixed(1)}s`);
    console.log(`  Succeeded: ${result.totalSucceeded}`);
    console.log(`  Failed:    ${result.totalFailed}`);

    if (result.totalFailed > 0) {
      process.exit(1);
    }
  });

program.parse();
