/**
 * Theme Context - provides dynamic theme based on emotional state throughout the app
 */

import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { useSharedValue, useAnimatedStyle, withTiming } from 'react-native-reanimated';
import { ThemeColors, EmotionalState, getThemeFromPAD, DEFAULT_THEME, emotionalStateToValues } from './theme';

interface ThemeContextValue {
  theme: ThemeColors;
  emotionalState: EmotionalState;
  updateMood: (trust?: number, valence?: number, arousal?: number, longing?: number) => void;
  // Animated values for smooth transitions
  primary: string;
  secondary: string;
  background: string;
  surface: string;
  text: string;
  muted: string;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

interface ThemeProviderProps {
  children: ReactNode;
  initialTrust?: number;
  initialValence?: number;
  initialArousal?: number;
  initialLonging?: number;
}

export function ThemeProvider({
  children,
  initialTrust,
  initialValence,
  initialArousal,
  initialLonging,
}: ThemeProviderProps) {
  const [emotionalState, setEmotionalState] = useState<EmotionalState>(() => ({
    trust: initialTrust ?? 0.5,
    valence: initialValence ?? 0,
    arousal: initialArousal ?? 0,
    longing: initialLonging ?? 0,
  }));

  const [theme, setTheme] = useState<ThemeColors>(() =>
    getThemeFromPAD(emotionalState)
  );

  // Shared values for smooth color transitions using Reanimated
  const primaryAnim = useSharedValue(DEFAULT_THEME.primary);
  const secondaryAnim = useSharedValue(DEFAULT_THEME.secondary);
  const backgroundAnim = useSharedValue(DEFAULT_THEME.background);
  const surfaceAnim = useSharedValue(DEFAULT_THEME.surface);
  const textAnim = useSharedValue(DEFAULT_THEME.text);
  const mutedAnim = useSharedValue(DEFAULT_THEME.muted);

  const updateMood = useCallback((
    trust?: number,
    valence?: number,
    arousal?: number,
    longing?: number
  ) => {
    setEmotionalState(prev => {
      const newState: EmotionalState = {
        trust: trust ?? prev.trust,
        valence: valence ?? prev.valence,
        arousal: arousal ?? prev.arousal,
        longing: longing ?? prev.longing,
      };

      // Calculate new theme
      const newTheme = getThemeFromPAD(newState);
      setTheme(newTheme);

      // Animate to new colors (200ms transition)
      primaryAnim.value = withTiming(newTheme.primary, { duration: 200 });
      secondaryAnim.value = withTiming(newTheme.secondary, { duration: 200 });
      backgroundAnim.value = withTiming(newTheme.background, { duration: 200 });
      surfaceAnim.value = withTiming(newTheme.surface, { duration: 200 });
      textAnim.value = withTiming(newTheme.text, { duration: 200 });
      mutedAnim.value = withTiming(newTheme.muted, { duration: 200 });

      return newState;
    });
  }, [primaryAnim, secondaryAnim, backgroundAnim, surfaceAnim, textAnim, mutedAnim]);

  // Initialize colors on mount
  useEffect(() => {
    primaryAnim.value = theme.primary;
    secondaryAnim.value = theme.secondary;
    backgroundAnim.value = theme.background;
    surfaceAnim.value = theme.surface;
    textAnim.value = theme.text;
    mutedAnim.value = theme.muted;
  }, []);

  const value: ThemeContextValue = {
    theme,
    emotionalState,
    updateMood,
    primary: theme.primary,
    secondary: theme.secondary,
    background: theme.background,
    surface: theme.surface,
    text: theme.text,
    muted: theme.muted,
  };

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
}

/**
 * Hook to access the current theme
 */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    // Return default theme if not wrapped in provider (for login/register screens before auth)
    return {
      theme: DEFAULT_THEME,
      emotionalState: {
        trust: 0.5,
        valence: 0,
        arousal: 0,
        longing: 0,
      },
      updateMood: () => {},
      ...DEFAULT_THEME,
    };
  }
  return context;
}

/**
 * Quick hook for just primary color (useful for small updates)
 */
export function usePrimaryColor(): string {
  const { primary } = useTheme();
  return primary;
}