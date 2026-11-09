import { techBlue } from './tech-blue';
import { neonPurple } from './neon-purple';
import { emeraldGreen } from './emerald-green';
import type { CardTheme, ThemeDefinition } from '../types/card';

const themes: Record<CardTheme, ThemeDefinition> = {
  'tech-blue': techBlue,
  'neon-purple': neonPurple,
  'emerald-green': emeraldGreen,
};

export function resolveTheme(themeName?: CardTheme): ThemeDefinition {
  return themes[themeName ?? 'tech-blue'];
}

export function getAllThemes(): ThemeDefinition[] {
  return Object.values(themes);
}
