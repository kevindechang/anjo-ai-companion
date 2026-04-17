import { useEffect } from 'react';
import { Platform, StyleSheet, View } from 'react-native';
import Reanimated, {
  useSharedValue,
  useAnimatedStyle,
  withTiming,
  withRepeat,
  withSequence,
  interpolateColor,
  interpolate,
  Easing,
  Extrapolation,
} from 'react-native-reanimated';
import {
  T_TRUST, V_COLORS, A_COLORS, L_COLORS,
  sampleHex, BLOB_CONFIGS, LUTS, CLOCK_INPUT_RANGE,
} from '../lib/orb-colors';

interface OrbProps {
  size?: number;
  trust?: number;
  valence?: number;
  arousal?: number;
  longing?: number;
  awaiting?: boolean;
}

const BLOB_SHADOW_COLORS = [
  sampleHex(T_TRUST,  0.5),
  sampleHex(V_COLORS, 0.5),
  sampleHex(A_COLORS, 0.5),
  sampleHex(L_COLORS, 0.5),
];

const BLOB_OPACITIES = [0.55, 0.50, 0.46, 0.42];

export function AnimatedOrb({
  size = 38,
  trust   = 0.5,
  valence = 0,
  arousal = 0,
  longing = 0,
  awaiting = false,
}: OrbProps) {
  const pulseFactor = useSharedValue(1);
  const clock = useSharedValue(0);

  const trustAnim = useSharedValue(trust);
  const valenceAnim = useSharedValue(valence * 0.5 + 0.5);
  const arousalAnim = useSharedValue(arousal * 0.5 + 0.5);
  const longingAnim = useSharedValue(longing);

  useEffect(() => {
    if (awaiting) {
      pulseFactor.value = withRepeat(
        withSequence(
          withTiming(1.12, { duration: 900, easing: Easing.inOut(Easing.ease) }),
          withTiming(1.0, { duration: 900, easing: Easing.inOut(Easing.ease) })
        ),
        -1, // infinite loop
        true // reverse
      );
    } else {
      pulseFactor.value = withTiming(1.0, { duration: 300 });
    }
  }, [awaiting]);

  useEffect(() => {
    clock.value = withRepeat(
      withSequence(
        withTiming(1, { duration: 8000, easing: Easing.linear }),
        withTiming(0, { duration: 8000, easing: Easing.linear })
      ),
      -1,
      false
    );
  }, []);

  useEffect(() => { trustAnim.value = withTiming(trust, { duration: 1800 }); }, [trust]);
  useEffect(() => { valenceAnim.value = withTiming(valence * 0.5 + 0.5, { duration: 1800 }); }, [valence]);
  useEffect(() => { arousalAnim.value = withTiming(arousal * 0.5 + 0.5, { duration: 1800 }); }, [arousal]);
  useEffect(() => { longingAnim.value = withTiming(longing, { duration: 1800 }); }, [longing]);

  const spread = (size / 2) * 0.30;

  const containerStyle = useAnimatedStyle(() => ({
    transform: [{ scale: pulseFactor.value }]
  }));

  return (
    <Reanimated.View style={containerStyle}>
      <View style={[styles.orb, { width: size, height: size, borderRadius: size / 2 }]}>
        {BLOB_CONFIGS.map((cfg, i) => {
          const blobDiameter = size * cfg.sizeFactor;
          const offset = (size - blobDiameter) / 2;
          const lut = LUTS[i];
          
          const iosShadow = Platform.OS === 'ios' ? {
            shadowColor: BLOB_SHADOW_COLORS[i],
            shadowOffset: { width: 0, height: 0 },
            shadowOpacity: 0.65,
            shadowRadius: size * 0.28,
          } : {};

          // Reanimated style calculation for each blob (runs natively at 60fps)
          const blobStyle = useAnimatedStyle(() => {
            const tx = interpolate(clock.value, CLOCK_INPUT_RANGE, lut.tx, Extrapolation.CLAMP) * spread;
            const ty = interpolate(clock.value, CLOCK_INPUT_RANGE, lut.ty, Extrapolation.CLAMP) * spread;

            let bgColor;
            if (i === 0) {
              bgColor = interpolateColor(trustAnim.value, [0, 0.5, 1], ['#4A6FA5', '#2EC4B6', '#E86F5C']);
            } else if (i === 1) {
              bgColor = interpolateColor(valenceAnim.value, [0, 0.5, 1], ['#7B6B9E', '#5AC8BE', '#F7D26A']);
            } else if (i === 2) {
              bgColor = interpolateColor(arousalAnim.value, [0, 0.5, 1], ['#2C3E6B', '#45B7A0', '#FF6F91']);
            } else {
              bgColor = interpolateColor(longingAnim.value, [0, 0.5, 1], ['#3D1A5C', '#C060C0', '#FFB0E8']);
            }

            return {
              backgroundColor: bgColor,
              transform: [{ translateX: tx }, { translateY: ty }],
            };
          });

          return (
            <Reanimated.View
              key={i}
              style={[
                {
                  position: 'absolute',
                  left: offset,
                  top: offset,
                  width: blobDiameter,
                  height: blobDiameter,
                  borderRadius: blobDiameter / 2,
                  opacity: BLOB_OPACITIES[i],
                },
                iosShadow as any,
                blobStyle,
              ]}
            />
          );
        })}
      </View>
    </Reanimated.View>
  );
}

const styles = StyleSheet.create({
  orb: {
    overflow: 'hidden',
    backgroundColor: '#100e0c',
  },
});
