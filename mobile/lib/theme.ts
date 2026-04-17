/**
 * Theme system based on PAD (Pleasure-Arousal-Dominance) emotional model
 * Maps emotional state to color palettes for dynamic UI theming
 */

export interface ThemeColors {
  primary: string;    // Main accent color (buttons, highlights)
  secondary: string;  // Borders, icons
  background: string; // App background
  surface: string;    // Cards, inputs
  text: string;       // Primary text
  muted: string;      // Placeholders, secondary text
}

// Emotional state from API (mood + attachment)
export interface EmotionalState {
  trust: number;      // 0-1 from relationship.trust_score
  valence: number;    // -1 to 1 from mood.valence
  arousal: number;    // -1 to 1 from mood.arousal
  longing: number;    // 0-1 from attachment.longing
}

// Base theme when no emotional data available
export const DEFAULT_THEME: ThemeColors = {
  primary: '#c9a96e',   // Gold
  secondary: '#d4b896', // Light gold
  background: '#0d0d0f',
  surface: '#1a1918',
  text: '#e6e2dc',
  muted: '#887f76',
};

// Predefined emotional themes
const THEMES: Record<string, ThemeColors> = {
  // High trust + positive valence = warm/loving
  loving: {
    primary: '#c9a96e',   // Gold
    secondary: '#d4b896',
    background: '#0d0d0f',
    surface: '#1a1918',
    text: '#e6e2dc',
    muted: '#887f76',
  },
  // Low trust + negative valence = cool/distant
  distant: {
    primary: '#4A6FA5',   // Steel Blue
    secondary: '#6B8CC5',
    background: '#0a0d14',
    surface: '#141a24',
    text: '#d4dce8',
    muted: '#6b7a8f',
  },
  // High arousal = excited
  excited: {
    primary: '#E86F5C',   // Coral
    secondary: '#f09080',
    background: '#140d0c',
    surface: '#241a18',
    text: '#f0e2dc',
    muted: '#9a7a72',
  },
  // Low arousal = calm/peaceful
  calm: {
    primary: '#45B7A0',   // Teal
    secondary: '#65d4bc',
    background: '#0c1412',
    surface: '#162420',
    text: '#d4f0e8',
    muted: '#6a9a8a',
  },
  // High longing = longing/purple
  longing: {
    primary: '#8B5CF6',   // Purple
    secondary: '#a78bfa',
    background: '#100d14',
    surface: '#1a1624',
    text: '#e8e0f0',
    muted: '#7a6a9a',
  },
  // Neutral balanced
  neutral: {
    primary: '#c9a96e',   // Gold (default)
    secondary: '#d4b896',
    background: '#0d0d0f',
    surface: '#1a1918',
    text: '#e6e2dc',
    muted: '#887f76',
  },
};

/**
 * Determine dominant emotional theme from PAD values
 */
function getDominantEmotion(state: EmotionalState): string {
  const { trust, valence, arousal, longing } = state;

  // High longing overrides other emotions
  if (longing > 0.7) return 'longing';

  // Low trust + negative valence = distant
  if (trust < 0.3 && valence < -0.3) return 'distant';

  // High arousal + positive valence = excited
  if (arousal > 0.5 && valence > 0.3) return 'excited';

  // Low arousal + positive valence = calm
  if (arousal < -0.3 && valence > 0.2) return 'calm';

  // High trust + positive valence = loving
  if (trust > 0.6 && valence > 0.3) return 'loving';

  return 'neutral';
}

/**
 * Linear interpolation between two hex colors
 */
function lerpColor(color1: string, color2: string, t: number): string {
  // Parse hex to RGB
  const parse = (hex: string) => {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return [r, g, b];
  };

  const [r1, g1, b1] = parse(color1);
  const [r2, g2, b2] = parse(color2);

  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const b = Math.round(b1 + (b2 - b1) * t);

  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}

/**
 * Get theme colors from emotional state
 * Interpolates between themes based on emotional intensity
 */
export function getThemeFromPAD(state: EmotionalState): ThemeColors {
  // Use default theme if no valid emotional data
  if (state.trust === undefined && state.valence === undefined) {
    return DEFAULT_THEME;
  }

  // Default values for any missing properties
  const normalized: EmotionalState = {
    trust: state.trust ?? 0.5,
    valence: state.valence ?? 0,
    arousal: state.arousal ?? 0,
    longing: state.longing ?? 0,
  };

  const emotion = getDominantEmotion(normalized);
  const theme = THEMES[emotion];

  // Calculate interpolation factor based on emotional intensity
  const intensity = Math.max(
    Math.abs(normalized.valence),
    Math.abs(normalized.arousal),
    normalized.longing
  );

  // Interpolate between the emotion theme and neutral
  const t = intensity * 0.7; // Scale to keep colors from getting too extreme

  return {
    primary: lerpColor(DEFAULT_THEME.primary, theme.primary, t),
    secondary: lerpColor(DEFAULT_THEME.secondary, theme.secondary, t),
    background: lerpColor(DEFAULT_THEME.background, theme.background, t * 0.5),
    surface: lerpColor(DEFAULT_THEME.surface, theme.surface, t * 0.5),
    text: lerpColor(DEFAULT_THEME.text, theme.text, t * 0.3),
    muted: lerpColor(DEFAULT_THEME.muted, theme.muted, t * 0.3),
  };
}

/**
 * Convert emotional state to theme values (for use in components)
 * Returns values in range expected by components
 */
export function emotionalStateToValues(
  trustScore?: number,
  longingVal?: number,
  valenceVal?: number,
  arousalVal?: number
): EmotionalState {
  return {
    trust: typeof trustScore === 'number' ? Math.max(0, Math.min(1, trustScore)) : 0.5,
    valence: typeof valenceVal === 'number' ? Math.max(-1, Math.min(1, valenceVal)) : 0,
    arousal: typeof arousalVal === 'number' ? Math.max(-1, Math.min(1, arousalVal)) : 0,
    longing: typeof longingVal === 'number' ? Math.max(0, Math.min(1, longingVal)) : 0,
  };
}