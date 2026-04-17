import { useEffect, useState, useMemo } from 'react';
import {
  ActivityIndicator, Linking, ScrollView,
  StyleSheet, Text, TouchableOpacity, View,
} from 'react-native';
import { Stack } from 'expo-router';
import { api, BillingStatus, BillingConfig } from '../../lib/api';
import { useTheme } from '../../lib/theme-context';

const PLANS = [
  { key: 'free', name: 'Free', price: '$0', period: '', features: ['50 messages / day', 'Basic memory', '7-day history'] },
  { key: 'pro', name: 'Pro', price: '$9', period: '/mo', features: ['300 messages / day', 'Deep memory', 'Full history', 'Letter from Anjo'] },
  { key: 'premium', name: 'Premium', price: '$19', period: '/mo', features: ['Unlimited messages', 'Everything in Pro', 'Priority responses'] },
];

const CREDIT_PACKS = [
  { key: 'credits_100', label: '100 messages', price: '$2' },
  { key: 'credits_500', label: '500 messages', price: '$8' },
  { key: 'credits_1000', label: '1000 messages', price: '$14' },
];

export default function Billing() {
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [config, setConfig] = useState<BillingConfig | null>(null);
  const [loading, setLoading] = useState(true);

  const { background: bg, primary, surface, surface2, border, text, muted, green } = useTheme();

  const C = useMemo(() => ({
    bg, surface, surface2, border, accent: primary, text, muted, green,
  }), [bg, surface, surface2, border, primary, green]);

  const styles = useMemo(() => StyleSheet.create({
    scroll: { flex: 1, backgroundColor: C.bg },
    content: { paddingHorizontal: 20, paddingTop: 16 },
    center: { flex: 1, backgroundColor: C.bg, alignItems: 'center', justifyContent: 'center', minHeight: 300 },
    sectionLabel: { fontSize: 11, fontWeight: '600', color: C.muted, letterSpacing: 1, marginBottom: 8, marginTop: 20 },
    card: { backgroundColor: C.surface, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, borderColor: C.border, padding: 16, marginBottom: 4 },
    planCurrentRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    planCurrentName: { fontSize: 17, fontWeight: '600', color: C.text },
    planCurrentSub: { fontSize: 13, color: C.muted, marginTop: 2 },
    manageBtn: { borderWidth: 1, borderColor: C.accent, borderRadius: 8, paddingHorizontal: 14, paddingVertical: 7 },
    manageBtnText: { fontSize: 13, color: C.accent, fontWeight: '500' },
    usageRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 },
    usageLabel: { fontSize: 13, color: C.muted },
    creditBadge: { fontSize: 12, color: C.green, fontWeight: '600' },
    barBg: { height: 4, backgroundColor: C.surface2, borderRadius: 2 },
    barFill: { height: 4, borderRadius: 2 },
    planCard: { backgroundColor: C.surface, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, borderColor: C.border, padding: 16, marginBottom: 8 },
    planCardActive: { borderColor: C.accent },
    planHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 },
    planName: { fontSize: 16, fontWeight: '600', color: C.text },
    planPriceRow: { flexDirection: 'row', alignItems: 'baseline', gap: 2 },
    planPrice: { fontSize: 20, fontWeight: '700', color: C.text },
    planPeriod: { fontSize: 13, color: C.muted },
    featureRow: { flexDirection: 'row', gap: 6, marginBottom: 4 },
    featureDot: { fontSize: 15, color: C.muted, lineHeight: 22 },
    featureText: { fontSize: 14, color: C.muted, lineHeight: 22 },
    upgradeBtn: { backgroundColor: C.accent, borderRadius: 10, paddingVertical: 11, alignItems: 'center', marginTop: 14 },
    upgradeBtnText: { fontSize: 14, fontWeight: '600', color: '#0f0d0c' },
    currentBadge: { backgroundColor: C.surface2, borderRadius: 10, paddingVertical: 9, alignItems: 'center', marginTop: 14 },
    currentBadgeText: { fontSize: 14, color: C.muted },
    creditRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.border },
    creditLabel: { fontSize: 15, color: C.text },
    creditRight: { flexDirection: 'row', alignItems: 'center', gap: 4 },
    creditPrice: { fontSize: 15, color: C.accent, fontWeight: '600' },
    creditArrow: { fontSize: 16, color: C.muted },
    bodyText: { fontSize: 14, color: C.muted, lineHeight: 20 },
    emptyText: { fontSize: 15, color: C.muted, textAlign: 'center' },
  }), [C]);

  useEffect(() => {
    Promise.all([api.billing.status().catch(() => null), api.billing.config().catch(() => null)]).then(([s, c]) => {
      setStatus(s); setConfig(c); setLoading(false);
    });
  }, []);

  function openPortal() {
    Linking.openURL(`${process.env.EXPO_PUBLIC_API_URL ?? 'https://your-domain.com'}/billing`);
  }

  function openCheckout(planKey: string) { Linking.openURL(`${process.env.EXPO_PUBLIC_API_URL ?? 'https://your-domain.com'}/billing?plan=${planKey}`); }

  if (loading) return <View style={styles.center}><ActivityIndicator color={C.accent} /></View>;

  const currentTier = status?.tier ?? 'free';
  const paymentsEnabled = config?.payments_enabled ?? false;

  return (
    <>
      <Stack.Screen options={{ title: 'Plans & billing', headerStyle: { backgroundColor: C.bg }, headerTintColor: C.accent, headerShadowVisible: false, headerBackTitle: 'Anjo' }} />
      <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
        <Text style={styles.sectionLabel}>CURRENT PLAN</Text>
        <View style={styles.card}>
          <View style={styles.planCurrentRow}>
            <View>
              <Text style={styles.planCurrentName}>{capitalize(currentTier)}</Text>
              {status?.period_end && <Text style={styles.planCurrentSub}>Renews {formatDate(status.period_end)}</Text>}
            </View>
            {status?.subscribed && paymentsEnabled && <TouchableOpacity style={styles.manageBtn} onPress={openPortal}><Text style={styles.manageBtnText}>Manage</Text></TouchableOpacity>}
          </View>
          {status && (
            <View style={{ marginTop: 14 }}>
              <View style={styles.usageRow}>
                <Text style={styles.usageLabel}>{status.messages_used} / {status.daily_limit} messages today</Text>
                {status.message_credits > 0 && <Text style={styles.creditBadge}>+{status.message_credits} credits</Text>}
              </View>
              <View style={styles.barBg}>
                <View style={[styles.barFill, { width: `${Math.min(100, Math.round((status.messages_used / status.daily_limit) * 100))}%` as any, backgroundColor: status.messages_remaining === 0 ? C.muted : C.green }]} />
              </View>
            </View>
          )}
        </View>

        {paymentsEnabled && (
          <>
            <Text style={styles.sectionLabel}>PLANS</Text>
            {PLANS.map((plan) => {
              const isCurrent = plan.key === currentTier;
              return (
                <View key={plan.key} style={[styles.planCard, isCurrent && styles.planCardActive]}>
                  <View style={styles.planHeader}>
                    <Text style={[styles.planName, isCurrent && { color: C.accent }]}>{plan.name}</Text>
                    <View style={styles.planPriceRow}><Text style={[styles.planPrice, isCurrent && { color: C.accent }]}>{plan.price}</Text>{plan.period && <Text style={styles.planPeriod}>{plan.period}</Text>}</View>
                  </View>
                  {plan.features.map((f, i) => <View key={i} style={styles.featureRow}><Text style={styles.featureDot}>·</Text><Text style={styles.featureText}>{f}</Text></View>)}
                  {!isCurrent && plan.key !== 'free' && <TouchableOpacity style={styles.upgradeBtn} onPress={() => openCheckout(plan.key)}><Text style={styles.upgradeBtnText}>Upgrade to {plan.name}</Text></TouchableOpacity>}
                  {isCurrent && <View style={styles.currentBadge}><Text style={styles.currentBadgeText}>Current plan</Text></View>}
                </View>
              );
            })}
            <Text style={styles.sectionLabel}>MESSAGE CREDITS</Text>
            <View style={styles.card}>
              <Text style={[styles.bodyText, { marginBottom: 12 }]}>Top up messages without changing your plan.</Text>
              {CREDIT_PACKS.map((pack) => (
                <TouchableOpacity key={pack.key} style={styles.creditRow} onPress={() => openCheckout(pack.key)}>
                  <Text style={styles.creditLabel}>{pack.label}</Text>
                  <View style={styles.creditRight}><Text style={styles.creditPrice}>{pack.price}</Text><Text style={styles.creditArrow}>›</Text></View>
                </TouchableOpacity>
              ))}
            </View>
          </>
        )}
        {!paymentsEnabled && <View style={styles.center}><Text style={styles.emptyText}>Billing is not enabled in this environment.</Text></View>}
        <View style={{ height: 40 }} />
      </ScrollView>
    </>
  );
}

function capitalize(s: string) { return s.charAt(0).toUpperCase() + s.slice(1); }
function formatDate(iso: string) { return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }); }