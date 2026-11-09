import React from 'react';
import ReactDOMServer from 'react-dom/server';
import { resolveTheme } from '../themes/index';
import { SvgKnowledgeCard } from '../components/svg/SvgKnowledgeCard';
import type { CardData } from '../types/card';
import * as fs from 'node:fs';
import * as path from 'node:path';

export async function generateSvg(
  cardData: CardData,
  outputDir: string,
  width: number = 1920,
  height: number = 1080,
): Promise<string> {
  const svgElement = React.createElement(SvgKnowledgeCard, {
    cardData,
    width,
    height,
  });

  const svgString = ReactDOMServer.renderToStaticMarkup(svgElement);

  const svgDir = path.join(outputDir, 'svg');
  fs.mkdirSync(svgDir, { recursive: true });

  const outputPath = path.join(svgDir, `${cardData.id}.svg`);
  fs.writeFileSync(outputPath, svgString, 'utf-8');

  return outputPath;
}
