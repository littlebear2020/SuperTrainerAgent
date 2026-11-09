import type { ThemeDefinition } from '../types/card';

export const emeraldGreen: ThemeDefinition = {
  name: 'emerald-green',
  displayName: 'Emerald Green',
  background: {
    primary: '#0a1f1a',
    secondary: '#134e3a',
    gradientStops: [
      { offset: 0, color: '#0a1f1a' },
      { offset: 0.5, color: '#134e3a' },
      { offset: 1, color: '#0d2e24' },
    ],
  },
  glass: {
    background: 'rgba(255, 255, 255, 0.07)',
    borderColor: 'rgba(110, 231, 183, 0.2)',
    blur: 16,
    shadow: '0 8px 32px rgba(0, 0, 0, 0.4)',
  },
  text: {
    primary: '#f0fdf4',
    secondary: 'rgba(187, 247, 208, 0.7)',
    accent: '#34d399',
  },
  glow: {
    color: 'rgba(52, 211, 153, 0.45)',
    intensity: 0.6,
    spread: 22,
  },
  pointBullet: '\u25CF',
  tagBackground: 'rgba(52, 211, 153, 0.2)',
  tagBorderColor: 'rgba(52, 211, 153, 0.35)',
};
