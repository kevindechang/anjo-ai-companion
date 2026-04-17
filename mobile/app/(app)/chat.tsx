import * as Haptics from 'expo-haptics';
import { useCallback, useEffect, useRef, useState, useMemo } from 'react';
import {
  Alert, Animated, KeyboardAvoidingView, Platform,
  StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Stack, useRouter } from 'expo-router';
import { BlurView } from 'expo-blur';
import Reanimated, { FadeInDown, FadeOut, LinearTransition } from 'react-native-reanimated';

import { api, Message } from '../../lib/api';
import { streamMessage } from '../../lib/sse';
import { clearAuth } from '../../lib/storage';
import { useAuth } from '../../lib/auth-context';
import { useTheme, ThemeContextValue } from '../../lib/theme-context';
import { AnimatedOrb } from '../../components/AnimatedOrb';

interface DisplayMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  streaming?: boolean;
  isSystem?: boolean;
}

function formatTs(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return time;
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} · ${time}`;
}

function StatusDot({ streaming, accentColor, greenColor }: { streaming: boolean; accentColor: string; greenColor: string }) {
  const pulse = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (!streaming) { pulse.setValue(1); return; }
    const anim = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 0.35, duration: 500, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 1,    duration: 500, useNativeDriver: true }),
      ]),
    );
    anim.start();
    return () => anim.stop();
  }, [streaming]);
  return (
    <Animated.View
      style={{
        width: 8, height: 8, borderRadius: 4,
        backgroundColor: streaming ? accentColor : greenColor,
        opacity: pulse,
        marginLeft: 6,
      }}
    />
  );
}

function TypingDots({ mutedColor }: { mutedColor: string }) {
  const anim = useRef([
    new Animated.Value(0),
    new Animated.Value(0),
    new Animated.Value(0),
  ]).current;

  useEffect(() => {
    const animations = anim.map((dot, i) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(i * 160),
          Animated.timing(dot, { toValue: 1, duration: 300, useNativeDriver: true }),
          Animated.timing(dot, { toValue: 0, duration: 300, useNativeDriver: true }),
          Animated.delay((2 - i) * 160),
        ]),
      ),
    );
    animations.forEach((a) => a.start());
    return () => animations.forEach((a) => a.stop());
  }, []);

  return (
    <View style={{ flexDirection: 'row', gap: 5, paddingVertical: 8, paddingHorizontal: 4 }}>
      {anim.map((dot, i) => (
        <Animated.View
          key={i}
          style={{
            width: 7, height: 7, borderRadius: 3.5, backgroundColor: mutedColor,
            opacity: dot.interpolate({ inputRange: [0, 1], outputRange: [0.3, 1] }),
            transform: [
              { translateY: dot.interpolate({ inputRange: [0, 1], outputRange: [0, -5] }) },
            ],
          }}
        />
      ))}
    </View>
  );
}

function SendIcon({ color }: { color: string }) {
  return <Text style={{ fontSize: 18, color, fontWeight: '700', lineHeight: 22 }}>↑</Text>;
}

const ReanimatedFlatList = Reanimated.createAnimatedComponent(View) as any;

export default function Chat() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const [orbTrust,   setOrbTrust]   = useState(0.3);
  const orbTrustRef = useRef(orbTrust);
  const [orbValence, setOrbValence] = useState(0);
  const [orbArousal, setOrbArousal] = useState(0);
  const [orbLonging, setOrbLonging] = useState(0);
  const orbLongingRef = useRef(orbLonging);

  useEffect(() => { orbTrustRef.current = orbTrust; }, [orbTrust]);
  useEffect(() => { orbLongingRef.current = orbLonging; }, [orbLonging]);

  const cancelStream = useRef<(() => void) | null>(null);
  const listRef = useRef<any>(null);
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { setAuthed } = useAuth();

  // Theme
  const { primary, surface, surface2, border, text, muted, background, updateMood } = useTheme();

  const C = useMemo(() => ({
    bg: background,
    surface: surface,
    surface2: surface2,
    border: border,
    accent: primary,
    accent2: primary,
    text: text,
    muted: muted,
    green: '#6dbf8a',
    danger: '#c97070',
    userText: background,
  }), [primary, surface, surface2, border, text, muted, background]);

  const styles = useMemo(() => StyleSheet.create({
    container: { flex: 1, backgroundColor: C.bg },
    headerAbsolute: {
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: 'rgba(255,255,255,0.05)',
    },
    headerContent: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 20,
      paddingBottom: 10,
    },
    headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 10 },
    headerName: { fontSize: 16, fontWeight: '600', color: C.accent },
    menuBtn: { padding: 8, borderRadius: 8, minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center' },
    dropdown: {
      position: 'absolute',
      top: 90,
      right: 16,
      backgroundColor: C.surface,
      borderWidth: 1,
      borderColor: C.border,
      borderRadius: 16,
      paddingVertical: 8,
      minWidth: 200,
      zIndex: 100,
      shadowColor: '#000',
      shadowOffset: { width: 0, height: 10 },
      shadowOpacity: 0.6,
      shadowRadius: 30,
      elevation: 20,
    },
    dropdownItem: { paddingVertical: 12, paddingHorizontal: 16 },
    dropdownText: { fontSize: 15, color: C.text, fontWeight: '500' },
    divider: { height: StyleSheet.hairlineWidth, backgroundColor: C.border, marginHorizontal: 8 },
    listContent: { paddingHorizontal: 16, gap: 16 },
    row: { flexDirection: 'row', alignItems: 'flex-end', gap: 8 },
    rowUser: { flexDirection: 'row-reverse' },
    rowAnjo: { flexDirection: 'row' },
    wrapUser: { alignItems: 'flex-end', maxWidth: '72%', gap: 3 },
    wrapAnjo: { alignItems: 'flex-start', maxWidth: '72%', gap: 3 },
    bubble: { borderRadius: 18, paddingHorizontal: 15, paddingVertical: 11 },
    bubbleUser: { backgroundColor: C.accent, borderBottomRightRadius: 5 },
    bubbleAnjo: {
      backgroundColor: C.surface,
      borderWidth: 1,
      borderColor: 'rgba(255,255,255,0.08)',
      borderBottomLeftRadius: 5,
    },
    bubbleSystem: { backgroundColor: C.surface2, borderColor: C.accent },
    bubbleText: { fontSize: 15, lineHeight: 24 },
    textUser: { color: C.userText, fontWeight: '500' },
    textAnjo: { color: C.text },
    timestamp: { fontSize: 11, color: C.muted, opacity: 0.7, paddingHorizontal: 4 },
    tsRight: { textAlign: 'right' },
    tsLeft: { textAlign: 'left' },
    inputAbsolute: {
      position: 'absolute',
      bottom: 0,
      left: 0,
      right: 0,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: 'rgba(255,255,255,0.05)',
    },
    inputBarContent: {
      flexDirection: 'row',
      alignItems: 'flex-end',
      paddingHorizontal: 16,
      paddingTop: 12,
      gap: 10,
    },
    textInput: {
      flex: 1,
      backgroundColor: 'rgba(26,25,24,0.8)',
      borderWidth: 1,
      borderColor: 'rgba(255,255,255,0.1)',
      borderRadius: 22,
      color: C.text,
      fontSize: 16,
      paddingHorizontal: 18,
      paddingVertical: 11,
      maxHeight: 140,
    },
    sendBtn: {
      width: 40, height: 40,
      borderRadius: 20,
      backgroundColor: C.accent,
      alignItems: 'center',
      justifyContent: 'center',
      marginBottom: 1,
      shadowColor: C.accent,
      shadowOffset: { width: 0, height: 2 },
      shadowOpacity: 0.35,
      shadowRadius: 8,
      elevation: 4,
    },
    sendDisabled: { opacity: 0.3, shadowOpacity: 0 },
    sheet: {
      flex: 1,
      backgroundColor: '#121110',
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

  useEffect(() => {
    (async () => {
      try {
        const tzOffset = new Date().getTimezoneOffset();
        const [{ history }, sessionData] = await Promise.all([
          api.chat.history(),
          api.chat.start(tzOffset),
        ]);
        setSessionId(sessionData.session_id);

        const histMsgs: DisplayMessage[] = history.map((m: Message, i: number) => ({
          id: String(i),
          role: m.role,
          content: m.content,
          timestamp: m.ts ?? m.timestamp,
        }));

        if (sessionData.pending_outreach) {
          const outreachMsg: DisplayMessage = {
            id: 'outreach_0',
            role: 'assistant',
            content: sessionData.pending_outreach,
            timestamp: new Date().toISOString(),
          };
          setMessages([...histMsgs, outreachMsg]);
        } else {
          setMessages(histMsgs);
        }

        api.story.memories().then((m) => {
          const trust = Math.max(0, Math.min(1, m.relationship.trust_score));
          const longing = Math.max(0, Math.min(1, m.attachment.longing));
          setOrbTrust(trust);
          setOrbLonging(longing);
          // Update theme based on loaded emotional state
          updateMood(trust, 0, 0, longing);
        }).catch(() => {});
      } catch { }
    })();
    return () => { cancelStream.current?.(); };
  }, [updateMood]);

  const scrollToBottom = useCallback(() => {
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 50);
  }, []);

  async function send() {
    if (!input.trim() || streaming || !sessionId) return;
    const text = input.trim();
    setInput('');
    setMenuOpen(false);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);

    const userMsg: DisplayMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    };
    const assistantId = `${Date.now()}_a`;
    const assistantMsg: DisplayMessage = { id: assistantId, role: 'assistant', content: '', streaming: true };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setStreaming(true);
    scrollToBottom();

    cancelStream.current = await streamMessage(sessionId, text, {
      onToken: (chunk) => {
        setMessages((prev) =>
          prev.map((m) => m.id === assistantId ? { ...m, content: m.content + chunk } : m),
        );
        scrollToBottom();
      },
      onDone: (fullText, _emotions, _intent, mood, attachment) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: fullText, streaming: false, timestamp: new Date().toISOString() }
              : m,
          ),
        );
        setStreaming(false);
        cancelStream.current = null;
        setOrbValence(mood.valence);
        setOrbArousal(mood.arousal);
        if (attachment.longing !== undefined) setOrbLonging(attachment.longing);
        // Update theme based on new emotional state
        updateMood(orbTrustRef.current, mood.valence, mood.arousal, attachment.longing ?? orbLongingRef.current);
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      },
      onSilent: () => {
        setMessages((prev) => [
          ...prev.filter((m) => m.id !== assistantId),
          {
            id: `${assistantId}_silent`,
            role: 'assistant',
            content: 'Anjo read the message and chose not to respond.',
            timestamp: new Date().toISOString(),
            isSystem: true,
          }
        ]);
        setStreaming(false);
        cancelStream.current = null;
      },
      onNoCredits: (tier) => {
        const upgradeMsg: DisplayMessage = {
          id: `${assistantId}_nocredits`,
          role: 'assistant',
          content: tier === 'free'
            ? "You've used all your free messages today. Upgrade to Pro for 300 messages a day."
            : "You've used all your messages for today. Come back tomorrow or grab a credit pack.",
          timestamp: new Date().toISOString(),
          isSystem: true,
        };
        setMessages((prev) => [
          ...prev.filter((m) => m.id !== assistantId),
          upgradeMsg,
        ]);
        setStreaming(false);
        cancelStream.current = null;
      },
      onError: () => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: 'Something went wrong. Try again.', streaming: false }
              : m,
          ),
        );
        setStreaming(false);
        cancelStream.current = null;
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      },
    });
  }

  async function signOut() {
    setMenuOpen(false);
    if (sessionId) await api.chat.end(sessionId).catch(() => {});
    await clearAuth();
    setAuthed(false);
  }

  function renderMessage({ item }: { item: DisplayMessage }) {
    const isUser = item.role === 'user';
    const showDots = !isUser && item.streaming && item.content === '';

    return (
      <Reanimated.View
        layout={LinearTransition}
        entering={FadeInDown}
        exiting={FadeOut}
        style={[styles.row, isUser ? styles.rowUser : styles.rowAnjo]}
      >
        {!isUser && <AnimatedOrb size={28} trust={orbTrust} valence={orbValence} arousal={orbArousal} longing={orbLonging} />}

        <View style={isUser ? styles.wrapUser : styles.wrapAnjo}>
          <View style={[
            styles.bubble,
            isUser ? styles.bubbleUser : styles.bubbleAnjo,
            item.isSystem && styles.bubbleSystem,
          ]}>
            {showDots ? (
              <TypingDots mutedColor={C.muted} />
            ) : (
              <Text style={[styles.bubbleText, isUser ? styles.textUser : styles.textAnjo]}>
                {item.content}
                {item.streaming && item.content !== '' && (
                  <Text style={{ color: C.muted }}>▌</Text>
                )}
              </Text>
            )}
          </View>
          {item.timestamp && !item.streaming && (
            <Text style={[styles.timestamp, isUser ? styles.tsRight : styles.tsLeft]}>
              {formatTs(item.timestamp)}
            </Text>
          )}
        </View>

        {isUser && <View style={{ width: 28 }} />}
      </Reanimated.View>
    );
  }

  const canSend = !!input.trim() && !streaming;
  const topPadding = insets.top + 60;
  const bottomPadding = insets.bottom + 100;

  return (
    <>
      <Stack.Screen options={{ headerShown: false }} />
      <KeyboardAvoidingView
        style={styles.container}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <Reanimated.FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(m: any) => m.id}
          renderItem={renderMessage}
          contentContainerStyle={[styles.listContent, { paddingTop: topPadding, paddingBottom: bottomPadding }]}
          onContentSizeChange={scrollToBottom}
          itemLayoutAnimation={LinearTransition}
          keyboardDismissMode="interactive"
        />

        {/* Absolute Glass Header */}
        <BlurView intensity={85} tint="dark" style={[styles.headerAbsolute, { paddingTop: insets.top + 10 }]}>
          <View style={styles.headerContent}>
            <View style={styles.headerLeft}>
              <AnimatedOrb size={38} trust={orbTrust} valence={orbValence} arousal={orbArousal} longing={orbLonging} awaiting={streaming} />
              <Text style={styles.headerName}>Anjo</Text>
              <StatusDot streaming={streaming} accentColor={C.accent} greenColor={C.green} />
            </View>
            <TouchableOpacity style={styles.menuBtn} onPress={() => setMenuOpen((v) => !v)}>
              <Text style={{ color: C.muted, fontSize: 20, lineHeight: 24 }}>···</Text>
            </TouchableOpacity>
          </View>
        </BlurView>

        {/* Dropdown Menu (Floating Absolute) */}
        {menuOpen && (
          <View style={styles.dropdown}>
            <TouchableOpacity
              style={styles.dropdownItem}
              onPress={() => { setMenuOpen(false); router.push('/(app)/story'); }}
            >
              <Text style={styles.dropdownText}>Our story</Text>
            </TouchableOpacity>
            <View style={styles.divider} />
            <TouchableOpacity
              style={styles.dropdownItem}
              onPress={() => { setMenuOpen(false); router.push('/(app)/billing'); }}
            >
              <Text style={styles.dropdownText}>Plans &amp; billing</Text>
            </TouchableOpacity>
            <View style={styles.divider} />
            <TouchableOpacity
              style={styles.dropdownItem}
              onPress={() => { setMenuOpen(false); router.push('/(app)/settings'); }}
            >
              <Text style={styles.dropdownText}>Settings</Text>
            </TouchableOpacity>
            <View style={styles.divider} />
            <TouchableOpacity
              style={styles.dropdownItem}
              onPress={() => { setMenuOpen(false); router.push('/(app)/forget'); }}
            >
              <Text style={[styles.dropdownText, { color: C.danger }]}>Forget me</Text>
            </TouchableOpacity>
            <View style={styles.divider} />
            <TouchableOpacity style={styles.dropdownItem} onPress={signOut}>
              <Text style={[styles.dropdownText, { color: C.danger }]}>Sign out</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* Absolute Glass Input Bar */}
        <BlurView intensity={85} tint="dark" style={[styles.inputAbsolute, { paddingBottom: insets.bottom + 10 }]}>
          <View style={styles.inputBarContent}>
            <TextInput
              style={styles.textInput}
              placeholder="Message Anjo…"
              placeholderTextColor={C.muted}
              value={input}
              onChangeText={setInput}
              multiline
              maxLength={2000}
            />
            <TouchableOpacity
              style={[styles.sendBtn, !canSend && styles.sendDisabled]}
              onPress={send}
              disabled={!canSend}
            >
              <SendIcon color={C.userText} />
            </TouchableOpacity>
          </View>
        </BlurView>

      </KeyboardAvoidingView>

    </>
  );
}
