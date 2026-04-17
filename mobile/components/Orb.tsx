import React, { useEffect, useRef } from 'react';
import { View, StyleSheet, Animated, Easing } from 'react-native';
import { useTheme } from '../lib/theme-context';

interface OrbProps {
  trust?: number;
  valence?: number;
  arousal?: number;
  longing?: number;
  size?: number;
  style?: object;
}

const Orb: React.FC<OrbProps> = ({
  trust: propTrust,
  valence: propValence,
  arousal: propArousal,
  longing: propLonging,
  size = 100,
  style
}) => {
  const theme = useTheme();
  const pulseAnim = useRef(new Animated.Value(1)).current;
  const glowAnim = useRef(new Animated.Value(0)).current;
  const wiggleAnim = useRef(new Animated.Value(0)).current;

  // Use prop values if provided, otherwise use theme values
  const trust = propTrust !== undefined ? propTrust : theme.trust;
  const valence = propValence !== undefined ? propValence : theme.valence;
  const arousal = propArousal !== undefined ? propArousal : theme.arousal;
  const longing = propLonging !== undefined ? propLonging : theme.longing;

  useEffect(() => {
    // Only update theme if using prop values
    if (propTrust !== undefined || propValence !== undefined ||
        propArousal !== undefined || propLonging !== undefined) {
      theme.updateMood(trust, valence, arousal, longing);
    }

    const pulseIntensity = 1 + (arousal * 0.3); // Increased range for more playful effect
    const glowIntensity = arousal;
    const wiggleIntensity = arousal * 0.5; // New casual wiggle effect

    Animated.parallel([
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, {
            toValue: pulseIntensity,
            duration: 800, // Faster for more playful feel
            easing: Easing.out(Easing.quad),
            useNativeDriver: true,
          }),
          Animated.timing(pulseAnim, {
            toValue: 1,
            duration: 800,
            easing: Easing.in(Easing.quad),
            useNativeDriver: true,
          }),
        ])
      ),
      Animated.loop(
        Animated.sequence([
          Animated.timing(wiggleAnim, {
            toValue: wiggleIntensity,
            duration: 1000,
            easing: Easing.inOut(Easing.quad),
            useNativeDriver: true,
          }),
          Animated.timing(wiggleAnim, {
            toValue: -wiggleIntensity,
            duration: 1000,
            easing: Easing.inOut(Easing.quad),
            useNativeDriver: true,
          }),
        ])
      ),
      Animated.timing(glowAnim, {
        toValue: glowIntensity,
        duration: 500,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: false,
      }),
    ]).start();
  }, [trust, valence, arousal, longing, theme, propTrust, propValence, propArousal, propLonging]);

  // Calculate wiggle rotation
  const rotate = wiggleAnim.interpolate({
    inputRange: [-1, 1],
    outputRange: ['-10deg', '10deg']
  });

  return (
    <View style={[styles.container, style]}>
      <Animated.View
        style={[
          styles.orb,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            backgroundColor: theme.primary,
            borderColor: theme.border,
            transform: [
              { scale: pulseAnim },
              { rotate }
            ],
            shadowColor: theme.primary,
            shadowOpacity: glowAnim.interpolate({
              inputRange: [0, 1],
              outputRange: [0.3, 0.8],
            }),
            shadowRadius: glowAnim.interpolate({
              inputRange: [0, 1],
              outputRange: [10, 25],
            }),
          },
        ]}
      >
        <View style={[
          styles.innerGlow,
          {
            backgroundColor: theme.primary,
            opacity: glowAnim,
            borderRadius: size / 2,
          }
        ]} />
      </Animated.View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  orb: {
    borderWidth: 2,
    borderStyle: 'solid',
    shadowOffset: { width: 0, height: 0 },
    elevation: 5,
  },
  innerGlow: {
    position: 'absolute',
    width: '100%',
    height: '100%',
    opacity: 0,
  },
});

export default Orb;
