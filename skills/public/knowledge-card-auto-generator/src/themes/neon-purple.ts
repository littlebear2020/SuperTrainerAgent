import type { ThemeDefinition } from '../types/card';

export const neonPurple: ThemeDefinition = {
  name: 'neon-purple',
  displayName: 'Neon Purple',
  background: {
    primary: '#1a0a2e',
    secondary: '#3d1a6e',
    gradientStops: [
      { offset: 0, color: '#1a0a2e' },
      { offset: 0.5, color: '#3d1a6e' },
      { offset: 1, color: '#2d1150' },
    ],
  },
  glass: {
    background: 'rgba(255, 255, 255, 0.06)',
    borderColor: 'rgba(200, 160, 255, 0.2)',
    blur: 18,
    shadow: '0 8px 32px rgba(0, 0, 0, 0.5)',
  },
  text: {
    primary: '#f5f0ff',
    secondary: 'rgba(210, 180, 255, 0.7)',
    accent: '#c084fc',
  },
  glow: {
    color: 'rgba(192, 132, 252, 0.5)',
    intensity: 0.7,
    spread: 25,
  },
  pointBullet: '\u25C6',
  tagBackground: 'rgba(192, 132, 252, 0.2)',
  tagBorderColor: 'rgba(192, 132, 252, 0.35)',
};
