import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      backdropBlur: {
        glass: '16px',
      },
      boxShadow: {
        glass: '0 8px 32px rgba(0, 0, 0, 0.37)',
        glow: '0 0 20px var(--glow-color, rgba(96, 165, 250, 0.4))',
      },
      backgroundColor: {
        glass: 'rgba(255, 255, 255, 0.08)',
      },
      borderColor: {
        glass: 'rgba(255, 255, 255, 0.15)',
      },
    },
  },
  plugins: [],
};

export default config;
