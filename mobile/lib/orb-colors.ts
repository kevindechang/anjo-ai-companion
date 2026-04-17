// Pure color math — no React, no Animated.
// Mirrors _hexRgb / _lerp / _sampleRgb from the web chat.html canvas orb.

export const T_TRUST  = ['#4A6FA5', '#2EC4B6', '#E86F5C'] as const;
export const V_COLORS = ['#7B6B9E', '#5AC8BE', '#F7D26A'] as const;
export const A_COLORS = ['#2C3E6B', '#45B7A0', '#FF6F91'] as const;
export const L_COLORS = ['#3D1A5C', '#C060C0', '#FFB0E8'] as const;
export const T_BG     = ['#0a0c12', '#0b0e0e', '#0f0d0b'] as const;

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function toHex(n: number): string {
  return Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0');
}

export function lerpHex(a: string, b: string, t: number): string {
  const [ar, ag, ab] = hexToRgb(a);
  const [br, bg, bb] = hexToRgb(b);
  return `#${toHex(ar + (br - ar) * t)}${toHex(ag + (bg - ag) * t)}${toHex(ab + (bb - ab) * t)}`;
}

/** Sample a 3-stop palette at val ∈ [0,1]. Returns a hex string. */
export function sampleHex(palette: readonly string[], val: number): string {
  const t = Math.max(0, Math.min(1, val));
  if (t <= 0.5) return lerpHex(palette[0], palette[1], t * 2);
  return lerpHex(palette[1], palette[2], (t - 0.5) * 2);
}

// ── Organic noise (direct port of web's _n2) ──────────────────────────────────

export function n2(x: number, y: number): number {
  return (
    Math.sin(x * 1.73 + y * 0.97) * 0.50 +
    Math.sin(x * 0.51 + y * 2.07) * 0.30 +
    Math.sin(x * 3.13 - y * 1.41) * 0.20
  );
}

// ── Blob configs (mirrors web exactly) ───────────────────────────────────────

export const BLOB_CONFIGS = [
  { phase: 0.0, rx: 0.62, ry: 0.55, sizeFactor: 0.82 },
  { phase: 2.4, rx: 0.58, ry: 0.62, sizeFactor: 0.78 },
  { phase: 4.8, rx: 0.50, ry: 0.58, sizeFactor: 0.74 },
  { phase: 1.2, rx: 0.54, ry: 0.48, sizeFactor: 0.68 },
] as const;

// ── LUT generation ────────────────────────────────────────────────────────────

const LUT_FRAMES = 64;

/**
 * Pre-compute drift LUT for one blob.
 * Returns { txNorm, tyNorm } arrays, each value ∈ [−1,+1].
 * Scale by (size/2 * spread) at render time.
 */
export function buildLut(blobIndex: number): { tx: number[]; ty: number[] } {
  const { phase, rx, ry } = BLOB_CONFIGS[blobIndex];
  const tx: number[] = [];
  const ty: number[] = [];
  // t_val range: 0 → 8.0 (matches ~2000 web frames at 0.004/frame)
  for (let k = 0; k < LUT_FRAMES; k++) {
    const t = (k / (LUT_FRAMES - 1)) * 8.0;
    tx.push(n2(t * rx + phase,       t * 0.31 + phase * 0.4));
    ty.push(n2(t * ry + phase + 1.7, t * 0.27 + phase * 0.6));
  }
  return { tx, ty };
}

// Pre-compute all 4 LUTs at module load (negligible cost, ~512 numbers)
export const LUTS = [0, 1, 2, 3].map(buildLut);

// inputRange for clock interpolation: [0, 1/63, 2/63, ..., 1]
export const CLOCK_INPUT_RANGE = Array.from(
  { length: LUT_FRAMES },
  (_, k) => k / (LUT_FRAMES - 1),
);
