import { useState, useMemo } from 'react';
import {
  Alert, KeyboardAvoidingView, Platform, StyleSheet,
  Text, TextInput, TouchableOpacity, View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { api } from '../../lib/api';
import { saveAuth } from '../../lib/storage';
import { useAuth } from '../../lib/auth-context';
import { useTheme } from '../../lib/theme-context';
import { AnimatedOrb } from '../../components/AnimatedOrb';

export default function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const { setAuthed } = useAuth();
  const { primary, surface, border, text, muted, background } = useTheme();

  const C = useMemo(() => ({
    bg: background,
    surface: surface,
    border: border,
    accent: primary,
    text: text,
    muted: muted,
    userText: background,
  }), [primary, surface, border, text, muted, background]);

  const styles = useMemo(() => StyleSheet.create({
    container: { flex: 1, backgroundColor: C.bg },
    inner: { flex: 1, justifyContent: 'center', paddingHorizontal: 32, alignItems: 'stretch' },
    orb: { alignSelf: 'center', marginBottom: 24 },
    wordmark: {
      fontSize: 36, fontWeight: '700',
      color: C.accent,
      textAlign: 'center',
      marginBottom: 8,
      letterSpacing: 0.5,
    },
    subtitle: { fontSize: 15, color: C.muted, textAlign: 'center', marginBottom: 36 },
    input: {
      backgroundColor: C.surface,
      borderWidth: 1,
      borderColor: C.border,
      borderRadius: 14,
      color: C.text,
      fontSize: 16,
      paddingHorizontal: 18,
      paddingVertical: 14,
      marginBottom: 12,
    },
    button: {
      backgroundColor: C.accent,
      borderRadius: 14,
      paddingVertical: 15,
      alignItems: 'center',
      marginTop: 8,
      marginBottom: 20,
      shadowColor: C.accent,
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.3,
      shadowRadius: 8,
      elevation: 4,
    },
    buttonDisabled: { opacity: 0.5 },
    buttonText: { color: C.userText, fontSize: 16, fontWeight: '600' },
    link: { color: C.muted, fontSize: 14, textAlign: 'center' },
  }), [C]);

  async function submit() {
    if (!username.trim() || !password) return;
    setLoading(true);
    try {
      const { token, user_id } = await api.auth.login(username.trim(), password);
      await saveAuth(token, user_id);
      setAuthed(true);
    } catch (e: any) {
      Alert.alert('Sign in failed', e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.inner}>
        {/* Orb */}
        <View style={styles.orb}>
          <AnimatedOrb size={64} />
        </View>

        <Text style={styles.wordmark}>Anjo</Text>
        <Text style={styles.subtitle}>Sign in to continue</Text>

        <TextInput
          style={styles.input}
          placeholder="Username"
          placeholderTextColor={C.muted}
          value={username}
          onChangeText={setUsername}
          autoCapitalize="none"
          autoCorrect={false}
        />
        <TextInput
          style={styles.input}
          placeholder="Password"
          placeholderTextColor={C.muted}
          value={password}
          onChangeText={setPassword}
          secureTextEntry
        />

        <TouchableOpacity style={[styles.button, loading && styles.buttonDisabled]} onPress={submit} disabled={loading}>
          <Text style={styles.buttonText}>{loading ? 'Signing in…' : 'Sign in'}</Text>
        </TouchableOpacity>

        <TouchableOpacity onPress={() => router.push('/(auth)/register')}>
          <Text style={styles.link}>No account? <Text style={{ color: C.accent }}>Register</Text></Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}