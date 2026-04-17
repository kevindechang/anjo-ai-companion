import { useState, useMemo } from 'react';
import { Alert, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { BlurView } from 'expo-blur';
import { api } from '../../lib/api';
import { clearAuth } from '../../lib/storage';
import { useAuth } from '../../lib/auth-context';
import { useTheme } from '../../lib/theme-context';

export default function Forget() {
  const [pw, setPw] = useState('');
  const [forgetLoading, setForgetLoading] = useState(false);
  const router = useRouter();
  const { setAuthed } = useAuth();
  
  const { primary, surface, surface2, border, text, muted, background } = useTheme();
  const C = useMemo(() => ({
    surface, surface2, border, accent: primary, text, muted, background
  }), [surface, surface2, border, primary]);

  const styles = useMemo(() => StyleSheet.create({
    sheet: {
      flex: 1,
      padding: 32,
      paddingTop: 48,
    },
    title: { fontSize: 24, fontWeight: '700', color: C.text, marginBottom: 12 },
    body: { fontSize: 16, color: C.muted, lineHeight: 24, marginBottom: 24 },
    input: {
      backgroundColor: C.surface, borderWidth: 1, borderColor: C.border,
      borderRadius: 12, color: C.text, fontSize: 16,
      paddingHorizontal: 16, paddingVertical: 16, marginBottom: 24,
    },
    btn: {
      borderRadius: 12, paddingVertical: 16,
      alignItems: 'center', marginBottom: 12,
    },
    btnDanger: { backgroundColor: '#4a1515' },
    btnCancel: { backgroundColor: C.surface2 },
    btnDisabled: { opacity: 0.4 },
    btnText: { fontSize: 16, fontWeight: '600', color: '#ffbbbb' },
  }), [C]);

  async function confirmForget() {
    if (!pw) return;
    setForgetLoading(true);
    try {
      await api.account.forget(pw);
      await clearAuth();
      setAuthed(false);
    } catch (e: any) {
      Alert.alert('Error', e.message);
      setForgetLoading(false);
    }
  }

  return (
    <BlurView intensity={90} tint="dark" style={styles.sheet}>
      <Text style={styles.title}>Forget me</Text>
      <Text style={styles.body}>
        Anjo will erase all memories, emotional residue, and your personality profile.
        Your account and login stay. This cannot be undone.
      </Text>
      <TextInput
        style={styles.input}
        placeholder="Confirm with password"
        placeholderTextColor={C.muted}
        value={pw}
        onChangeText={setPw}
        secureTextEntry
      />
      <TouchableOpacity
        style={[styles.btn, styles.btnDanger, forgetLoading && styles.btnDisabled]}
        onPress={confirmForget}
        disabled={forgetLoading}
      >
        <Text style={styles.btnText}>Forget everything</Text>
      </TouchableOpacity>
      <TouchableOpacity style={[styles.btn, styles.btnCancel]} onPress={() => router.back()}>
        <Text style={[styles.btnText, { color: C.muted }]}>Cancel</Text>
      </TouchableOpacity>
    </BlurView>
  );
}
