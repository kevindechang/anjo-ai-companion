import { getToken } from './storage';

export const API_BASE = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

async function authHeaders(): Promise<Record<string, string>> {
  const token = await getToken();
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: await authHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((err as any).detail ?? 'Request failed');
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: await authHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((err as any).detail ?? 'Request failed');
  }
  return res.json() as Promise<T>;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export interface AuthResult {
  token: string;
  user_id: string;
}
export interface VerifyResult {
  message: string;
}

// ── Chat ──────────────────────────────────────────────────────────────────────

export interface Message {
  role: 'user' | 'assistant';
  content: string;
  ts?: string;
  timestamp?: string;
}

export interface SessionStart {
  session_id: string;
  pending_outreach?: string;
}

// ── Account ───────────────────────────────────────────────────────────────────

export interface AccountInfo {
  user_id: string;
  username: string;
  email: string;
}

// ── Story ─────────────────────────────────────────────────────────────────────

export interface EmotionalResidue {
  emotion: string;
  intensity: number;
  source: string;
}

export interface StoryMemories {
  opinion: string | null;
  emotional_residue: EmotionalResidue[];
  desires: string[];
  attachment: {
    weight: number;
    texture: string;
    longing: number;
    comfort: number;
  };
  notes: string[];
  relationship: {
    stage: string;
    session_count: number;
    trust_score: number;
    user_name: string | null;
  };
}

export interface SessionEntry {
  timestamp?: string;
  summary?: string;
  emotional_tone?: string;
  topics?: string[];
  significance?: number;
  emotional_valence?: number;
}

export interface MemoryNode {
  id: string;
  content: string;
  confidence: number;
  created_at: string;
  type: 'fact' | 'preference' | 'commitment' | 'thread' | 'contradiction';
}

export interface MemoryGraphResponse {
  memory_graph: Record<string, MemoryNode[]>;
}

export interface LetterResponse {
  locked: boolean;
  letter?: string;
  session_count?: number;
}

// ── Billing ───────────────────────────────────────────────────────────────────

export interface BillingStatus {
  tier: string;
  subscribed: boolean;
  daily_limit: number;
  messages_used: number;
  messages_remaining: number;
  message_credits: number;
  period_end: string;
  payments_enabled: boolean;
}

export interface RCProduct {
  product_id: string;
  tier?: string;
  interval?: string;
  type?: string;
  amount?: number;
}

export interface BillingConfig {
  payments_enabled: boolean;
  user_id?: string;
  products?: Record<string, RCProduct>;
}

// ── API surface ───────────────────────────────────────────────────────────────

export const api = {
  auth: {
    login: (username: string, password: string) =>
      post<AuthResult>('/api/auth/login', { username, password }),
    register: (username: string, password: string, email?: string) =>
      post<AuthResult | VerifyResult>('/api/auth/register', { username, password, email: email ?? '' }),
  },

  chat: {
    start: (tzOffset?: number) =>
      post<SessionStart>(`/api/chat/start${tzOffset !== undefined ? `?tz_offset=${tzOffset}` : ''}`, {}),
    history: () => get<{ history: Message[] }>('/api/chat/history'),
    end: (sessionId: string) =>
      post<{ ok: boolean; reflected: boolean }>(`/api/chat/${sessionId}/end`, {}),
  },

  story: {
    memories: () => get<StoryMemories>('/api/story/memories'),
    sessions: () => get<{ sessions: SessionEntry[] }>('/api/story/sessions'),
    letter: () => get<LetterResponse>('/api/story/letter'),
    memoryGraph: () => get<MemoryGraphResponse>('/api/story/memory-graph'),
    deleteMemoryNode: async (nodeId: string) => {
      const res = await fetch(`${API_BASE}/api/story/memory-graph/${nodeId}`, {
        method: 'DELETE',
        headers: await authHeaders(),
      });
      if (!res.ok) throw new Error('Delete failed');
      return res.json();
    },
    bulkDeleteMemoryNodes: (startDate: string, endDate: string) =>
      post<{ ok: boolean; deleted_count: number }>(`/api/story/memory-graph/bulk-delete?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`, {}),
  },

  billing: {
    status: () => get<BillingStatus>('/api/billing/status'),
    config: () => get<BillingConfig>('/api/billing/config'),
  },

  account: {
    info: () => get<AccountInfo>('/api/account'),
    updateUsername: (username: string, password: string) =>
      post<{ ok: boolean }>('/api/account/update-username', { username, password }),
    updateEmail: (email: string, password: string) =>
      post<{ ok: boolean }>('/api/account/update-email', { email, password }),
    changePassword: (current_password: string, new_password: string) =>
      post<{ ok: boolean }>('/api/account/change-password', { current_password, new_password }),
    forget: (password: string) =>
      post<{ response: string }>('/api/forget', { password }),
    delete: (password: string) =>
      post<{ ok: boolean }>('/api/account/delete', { password }),
  },

  reflection: {
    log: () => get<{ entries: any[] }>('/api/reflection-log'),
  },
};
