import { createContext, useContext, useState, useEffect } from 'react';

export interface ThemeContextValue {
  primary: string;
  surface: string;
  surface2: string;
  border: string;
  text: string;
  muted: string;
  background: string;
  trust: number;
  valence: number;
  arousal: number;
  longing: number;
  updateMood: (trust: number, valence: number, arousal: number, longing: number) => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

export const ThemeProvider: React.FC<{children: React.ReactNode}> = ({ children }) => {
  const [trust, setTrust] = useState(0.5);
  const [valence, setValence] = useState(0);
  const [arousal, setArousal] = useState(0.5);
  const [longing, setLonging] = useState(0.5);

  const updateMood = (newTrust: number, newValence: number, newArousal: number, newLonging: number) => {
    setTrust(Math.max(0, Math.min(1, newTrust)));
    setValence(Math.max(-1, Math.min(1, newValence)));
    setArousal(Math.max(0, Math.min(1, newArousal)));
    setLonging(Math.max(0, Math.min(1, newLonging)));
  };

  // Calculate theme colors based on mood parameters
  const getThemeColors = () => {
    // Primary color based on valence (-1 to 1)
    const primaryHue = 200 + (valence * 100);
    const primarySaturation = 80 + (trust * 20);
    const primaryLightness = 50 + (longing * 10);
    const primary = `hsl(${primaryHue}, ${primarySaturation}%, ${primaryLightness}%)`;

    // Surface colors with adjusted lightness based on trust
    const surfaceLightness = 10 + (trust * 10);
    const surface = `hsl(0, 0%, ${surfaceLightness}%)`;
    const surface2 = `hsl(0, 0%, ${surfaceLightness + 5}%)`;

    // Border color with adjusted opacity based on arousal
    const borderOpacity = 0.2 + (arousal * 0.3);
    const border = `hsla(0, 0%, 100%, ${borderOpacity})`;

    // Text colors
    const text = `hsl(0, 0%, ${90 - (trust * 20)}%)`;
    const muted = `hsl(0, 0%, ${60 - (trust * 20)}%)`;

    // Background color
    const background = `hsl(0, 0%, ${5 + (trust * 5)}%)`;

    return {
      primary,
      surface,
      surface2,
      border,
      text,
      muted,
      background
    };
  };

  const theme = {
    ...getThemeColors(),
    trust,
    valence,
    arousal,
    longing,
    updateMood
  };

  return (
    <ThemeContext.Provider value={theme}>
      {children}
    </ThemeContext.Provider>
  );
};

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}

export default ThemeContext;
