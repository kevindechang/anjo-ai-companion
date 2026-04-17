import { useEffect, useState, useMemo } from 'react';
import {
  ActivityIndicator, ScrollView, StyleSheet, Text,
  TouchableOpacity, View,
} from 'react-native';
import { Stack } from 'expo-router';
import ReanimatedSwipeable from 'react-native-gesture-handler/ReanimatedSwipeable';
import Reanimated, { FadeOut, LinearTransition } from 'react-native-reanimated';
import { api, StoryMemories, SessionEntry, LetterResponse, MemoryNode } from '../../lib/api';
import { useTheme } from '../../lib/theme-context';

type Tab = 'memories' | 'timeline' | 'letter';

const STAGE_LABELS: Record<string, string> = {
  stranger:   'Stranger',
  acquaintance:'Acquaintance',
  friend:     'Friend',
  close:      'Close',
  intimate:   'Intimate',
};

function formatDate(iso?: string) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

function EmptyState({ text }: { text: string }) {
  const { muted } = useTheme();
  return <Text style={{ fontSize: 15, color: muted, textAlign: 'center', lineHeight: 22, padding: 32 }}>{text}</Text>;
}

export default function Story() {
  const [tab, setTab] = useState<Tab>('memories');
  const [memories, setMemories] = useState<StoryMemories | null>(null);
  const [sessions, setSessions] = useState<SessionEntry[]>([]);
  const [letter, setLetter] = useState<LetterResponse | null>(null);
  const [memoryGraph, setMemoryGraph] = useState<Record<string, MemoryNode[]>>({});
  const [loading, setLoading] = useState(true);

  const { background: bg, primary, surface, surface2, border, text, muted, danger, green } = useTheme();

  const C = useMemo(() => ({
    bg, surface, surface2, border, accent: primary, text, muted, danger, green,
  }), [bg, surface, surface2, border, primary, text, muted, danger, green]);

  const styles = useMemo(() => StyleSheet.create({
    scroll: { flex: 1, backgroundColor: C.bg },
    content: { paddingHorizontal: 20, paddingTop: 16, paddingBottom: 40 },
    center: { flex: 1, backgroundColor: C.bg, alignItems: 'center', justifyContent: 'center', padding: 32, minHeight: 300 },
    tabBar: { flexDirection: 'row', backgroundColor: C.bg, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: C.border },
    tab: { flex: 1, paddingVertical: 13, alignItems: 'center' },
    tabActive: { borderBottomWidth: 2, borderBottomColor: C.accent },
    tabText: { fontSize: 13, color: C.muted, fontWeight: '500' },
    tabTextActive: { color: C.accent },
    sectionLabel: { fontSize: 11, fontWeight: '600', color: C.muted, letterSpacing: 1, marginBottom: 8, marginTop: 20 },
    subSectionLabel: { fontSize: 9, fontWeight: '700', color: C.muted, letterSpacing: 0.5, marginBottom: 6, marginLeft: 4 },
    card: { backgroundColor: C.surface, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, borderColor: C.border, padding: 16, marginBottom: 4 },
    nodeRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 12, paddingHorizontal: 8, backgroundColor: C.surface },
    nodeBorder: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: C.border },
    nodeText: { fontSize: 14, color: C.text, flex: 1, marginRight: 10 },
    swipeDeleteBtn: { backgroundColor: C.danger, justifyContent: 'center', alignItems: 'flex-end', paddingHorizontal: 20 },
    swipeDeleteText: { color: '#fff', fontWeight: '600', fontSize: 14 },
    statsRow: { flexDirection: 'row', justifyContent: 'space-around' },
    stat: { alignItems: 'center' },
    statValue: { fontSize: 20, fontWeight: '700', color: C.accent },
    statLabel: { fontSize: 12, color: C.muted, marginTop: 2 },
    bodyText: { fontSize: 15, color: C.text, lineHeight: 22 },
    residueRow: {},
    residueHeader: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 5 },
    residueLabel: { fontSize: 14, color: C.text, fontWeight: '500', textTransform: 'capitalize' },
    residueSource: { fontSize: 12, color: C.muted },
    barBg: { height: 4, backgroundColor: C.surface2, borderRadius: 2 },
    barFill: { height: 4, backgroundColor: C.accent, borderRadius: 2 },
    tagsWrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
    tag: { backgroundColor: C.surface2, borderWidth: StyleSheet.hairlineWidth, borderColor: C.border, borderRadius: 20, paddingHorizontal: 12, paddingVertical: 6 },
    tagText: { fontSize: 13, color: C.text },
    timelineRow: { flexDirection: 'row', gap: 14, marginBottom: 0 },
    timelineDotCol: { alignItems: 'center', width: 14 },
    timelineDot: { width: 14, height: 14, borderRadius: 7, marginTop: 3 },
    timelineLine: { flex: 1, width: 1, backgroundColor: C.border, marginVertical: 4, minHeight: 20 },
    timelineContent: { flex: 1, paddingBottom: 20 },
    timelineHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 },
    timelineDate: { fontSize: 13, color: C.muted },
    toneChip: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 8, paddingVertical: 2 },
    toneChipText: { fontSize: 11, fontWeight: '600' },
    timelineSummary: { fontSize: 14, color: C.text, lineHeight: 20, marginBottom: 8 },
    topicsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
    topicChip: { backgroundColor: C.surface, borderWidth: StyleSheet.hairlineWidth, borderColor: C.border, borderRadius: 10, paddingHorizontal: 8, paddingVertical: 3 },
    topicChipText: { fontSize: 11, color: C.muted },
    letterCard: { backgroundColor: C.surface, borderRadius: 14, borderWidth: StyleSheet.hairlineWidth, borderColor: C.border, padding: 24 },
    letterText: { fontSize: 15, color: C.text, lineHeight: 26, fontStyle: 'italic' },
    lockIcon: { fontSize: 36, color: C.accent, marginBottom: 16 },
    lockTitle: { fontSize: 20, fontWeight: '600', color: C.text, marginBottom: 10 },
    lockBody: { fontSize: 15, color: C.muted, textAlign: 'center', lineHeight: 22 },
    emptyText: { fontSize: 15, color: C.muted, textAlign: 'center', lineHeight: 22 },
  }), [C]);

  const TONE_COLORS = useMemo(() => ({
    positive: C.green,
    negative: C.danger,
    neutral:  C.muted,
    mixed:    C.accent,
  }), [C]);

  const fetchData = () => {
    Promise.all([
      api.story.memories().catch(() => null),
      api.story.sessions().catch(() => ({ sessions: [] })),
      api.story.letter().catch(() => null),
      api.story.memoryGraph().catch(() => ({ memory_graph: {} })),
    ]).then(([mem, sess, let_, graph]) => {
      setMemories(mem);
      setSessions(sess?.sessions ?? []);
      setLetter(let_);
      setMemoryGraph(graph?.memory_graph ?? {});
      setLoading(false);
    });
  };

  useEffect(() => { fetchData(); }, []);

  const stageNum = () => {
    const stages = ['stranger', 'acquaintance', 'friend', 'close', 'intimate'];
    return stages.indexOf(memories?.relationship?.stage ?? 'stranger');
  };

  const toneColor = (tone?: string) => {
    if (!tone) return C.muted;
    const key = Object.keys(TONE_COLORS).find((k) => tone.toLowerCase().includes(k));
    return key ? TONE_COLORS[key] : C.muted;
  };

  return (
    <>
      <Stack.Screen
        options={{
          title: 'Our story',
          headerStyle: { backgroundColor: C.bg },
          headerTintColor: C.accent,
          headerShadowVisible: false,
          headerBackTitle: 'Anjo',
        }}
      />

      {/* Tab bar */}
      <View style={styles.tabBar}>
        {(['memories', 'timeline', 'letter'] as Tab[]).map((t) => (
          <TouchableOpacity key={t} style={[styles.tab, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t === 'memories' ? 'Memories' : t === 'timeline' ? 'Timeline' : 'Letter'}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator color={C.accent} /></View>
      ) : (
        <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
          {tab === 'memories' && <MemoriesTab memories={memories} stageNum={stageNum()} memoryGraph={memoryGraph} onRefresh={fetchData} styles={styles} C={C} toneColor={toneColor} />}
          {tab === 'timeline' && <TimelineTab sessions={sessions} styles={styles} C={C} toneColor={toneColor} />}
          {tab === 'letter' && <LetterTab letter={letter} styles={styles} C={C} />}
        </ScrollView>
      )}
    </>
  );
}

// ── Memories tab ──────────────────────────────────────────────────────────────

function Stat({ label, value, styles, accent }: { label: string; value: string; styles: any; accent: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

function MemoriesTab({ memories, stageNum, memoryGraph, onRefresh, styles, C, toneColor }: any) {
  const handleDeleteNode = async (nodeId: string) => {
    try { await api.story.deleteMemoryNode(nodeId); onRefresh(); } catch (e) { console.error(e); }
  };

  if (!memories) return <EmptyState text="No memories yet. Start a conversation with Anjo." />;

  const { relationship, opinion, emotional_residue, desires, attachment } = memories;

  const renderNode = (node: MemoryNode, type: string) => (
    <ReanimatedSwipeable renderRightActions={() => (
      <TouchableOpacity style={styles.swipeDeleteBtn} onPress={() => handleDeleteNode(node.id)}>
        <Text style={styles.swipeDeleteText}>Delete</Text>
      </TouchableOpacity>
    )} key={node.id}>
      <View style={[styles.nodeRow, styles.nodeBorder]}>
        <Text style={styles.nodeText}>{node.content}</Text>
      </View>
    </ReanimatedSwipeable>
  );

  return (
    <>
      <Text style={styles.sectionLabel}>RELATIONSHIP</Text>
      <View style={styles.card}>
        <View style={styles.statsRow}>
          <Stat label="Stage" value={STAGE_LABELS[relationship.stage] ?? relationship.stage} styles={styles} accent={C.accent} />
          <Stat label="Sessions" value={String(relationship.session_count)} styles={styles} accent={C.accent} />
          <Stat label="Trust" value={`${Math.round(relationship.trust_score * 100)}%`} styles={styles} accent={C.accent} />
        </View>
      </View>

      <Text style={styles.sectionLabel}>OPINION OF YOU</Text>
      <View style={styles.card}><Text style={styles.bodyText}>{opinion ?? "Anjo hasn't formed an opinion yet."}</Text></View>

      <Text style={styles.sectionLabel}>EMOTIONAL RESIDUE</Text>
      {emotional_residue?.length ? emotional_residue.map((r: any, i: number) => (
        <View key={i} style={styles.card}>
          <View style={styles.residueHeader}>
            <Text style={styles.residueLabel}>{r.emotion}</Text>
            <Text style={styles.residueSource}>{r.source}</Text>
          </View>
          <View style={styles.barBg}><View style={[styles.barFill, { width: `${r.intensity * 100}%` }]} /></View>
        </View>
      )) : <View style={styles.card}><Text style={styles.bodyText}>No emotional residue yet.</Text></View>}

      <Text style={styles.sectionLabel}>DESIRES</Text>
      <View style={styles.card}>
        <View style={styles.tagsWrap}>
          {desires?.length ? desires.map((d: string, i: number) => <View key={i} style={styles.tag}><Text style={styles.tagText}>{d}</Text></View>) : <Text style={styles.bodyText}>No desires recorded yet.</Text>}
        </View>
      </View>

      <Text style={styles.sectionLabel}>ATTACHMENT</Text>
      <View style={styles.card}>
        <View style={styles.statsRow}>
          <Stat label="Longing" value={`${Math.round((attachment?.longing ?? 0) * 100)}%`} styles={styles} accent={C.accent} />
          <Stat label="Comfort" value={`${Math.round((attachment?.comfort ?? 0) * 100)}%`} styles={styles} accent={C.accent} />
        </View>
      </View>

      {Object.entries(memoryGraph).map(([type, nodes]) => (
        <View key={type}>
          <Text style={styles.sectionLabel}>{type.toUpperCase()}</Text>
          <View style={styles.card}>{nodes?.length ? nodes.map((n) => renderNode(n, type)) : <Text style={styles.bodyText}>No {type} nodes.</Text>}</View>
        </View>
      ))}
    </>
  );
}

// ── Timeline tab ──────────────────────────────────────────────────────────────

function TimelineTab({ sessions, styles, C, toneColor }: any) {
  if (!sessions?.length) return <EmptyState text="No sessions yet." />;

  return (
    <View style={{ paddingTop: 8 }}>
      {sessions.map((sess: SessionEntry, i: number) => (
        <View key={i} style={styles.timelineRow}>
          <View style={styles.timelineDotCol}>
            <View style={[styles.timelineDot, { backgroundColor: toneColor(sess.emotional_tone) }]} />
            {i < sessions.length - 1 && <View style={styles.timelineLine} />}
          </View>
          <View style={styles.timelineContent}>
            <View style={styles.timelineHeader}>
              <Text style={styles.timelineDate}>{formatDate(sess.timestamp)}</Text>
              {sess.emotional_tone && (
                <View style={[styles.toneChip, { borderColor: toneColor(sess.emotional_tone) }]}>
                  <Text style={[styles.toneChipText, { color: toneColor(sess.emotional_tone) }]}>{sess.emotional_tone}</Text>
                </View>
              )}
            </View>
            <Text style={styles.timelineSummary}>{sess.summary}</Text>
            {sess.topics?.length > 0 && (
              <View style={styles.topicsRow}>
                {sess.topics.map((t: string, j: number) => (
                  <View key={j} style={styles.topicChip}><Text style={styles.topicChipText}>{t}</Text></View>
                ))}
              </View>
            )}
          </View>
        </View>
      ))}
    </View>
  );
}

// ── Letter tab ──────────────────────────────────────────────────────────────

function LetterTab({ letter, styles, C }: any) {
  if (!letter) return <EmptyState text="No letter yet." />;

  if (letter.locked) {
    return (
      <View style={{ alignItems: 'center', paddingTop: 60 }}>
        <Text style={styles.lockIcon}>🔒</Text>
        <Text style={styles.lockTitle}>Letter Locked</Text>
        <Text style={styles.lockBody}>Your letter will unlock at a meaningful moment in your journey together.</Text>
      </View>
    );
  }

  return (
    <View style={styles.letterCard}>
      <Text style={styles.letterText}>{letter.letter}</Text>
    </View>
  );
}