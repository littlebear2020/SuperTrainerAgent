export type CardTheme = 'tech-blue' | 'neon-purple' | 'emerald-green';

export type CardMetric = {
  icon: string;
  label: string;
  value: string;
};

export type CardSection = {
  title: string;
  points: string[];
};

export type CardFooter = {
  text: string;
  link?: string;
  badges?: Array<{ label: string; color?: string }>;
};

export type CardData = {
  id: string;
  title: string;
  icon?: string;
  subtitle?: string;
  points: string[];
  theme?: CardTheme;
  tags?: string[];
  metrics?: CardMetric[];
  sections?: CardSection[];
  footer?: CardFooter;
};

export type CardOutputFormat = 'svg' | 'mp4' | 'both';

export type CardOutput = {
  id: string;
  svgPath?: string;
  mp4Path?: string;
  success: boolean;
  error?: string;
};

export type CardSize = {
  width: number;
  height: number;
};

export type ThemeDefinition = {
  name: CardTheme;
  displayName: string;
  background: {
    primary: string;
    secondary: string;
    gradientStops: Array<{ offset: number; color: string }>;
  };
  glass: {
    background: string;
    borderColor: string;
    blur: number;
    shadow: string;
  };
  text: {
    primary: string;
    secondary: string;
    accent: string;
  };
  glow: {
    color: string;
    intensity: number;
    spread: number;
  };
  pointBullet: string;
  tagBackground: string;
  tagBorderColor: string;
};
