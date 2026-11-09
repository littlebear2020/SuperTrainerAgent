import type { ThemeDefinition } from '../types/card';

export const techBlue: ThemeDefinition = {
  name: 'tech-blue',
  displayName: 'Tech Blue',
  background: {
    primary: '#0a1628',
    secondary: '#1a365d',
    gradientStops: [
      { offset: 0, color: '#0a1628' },
      { offset: 0.5, color: '#1a365d' },
      { offset: 1, color: '#0f2847' },
    ],
  },
  glass: {
    background: 'rgba(255, 255, 255, 0.08)',
    borderColor: 'rgba(255, 255, 255, 0.15)',
    blur: 16,
    shadow: '0 8px 32px rgba(0, 0, 0, 0.37)',
  },
  text: {
    primary: '#f0f4ff',
    secondary: 'rgba(176, 199, 255, 0.7)',
    accent: '#60a5fa',
  },
  glow: {
    color: 'rgba(96, 165, 250, 0.4)',
    intensity: 0.6,
    spread: 20,
  },
  pointBullet: '\u25B8',
  tagBackground: 'rgba(96, 165, 250, 0.2)',
  tagBorderColor: 'rgba(96, 165, 250, 0.35)',
};
