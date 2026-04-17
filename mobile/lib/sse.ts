import { getToken } from './storage';
import { API_BASE } from './api';

export interface MoodState {
  valence: number;
  arousal: number;
  dominance: number;
}

export interface AttachmentState {
  longing: number;
  weight: number;
}

const DEFAULT_MOOD: MoodState = { valence: 0, arousal: 0, dominance: 0 };
const DEFAULT_ATTACH: AttachmentState = { longing: 0, weight: 0.5 };

export interface StreamCallbacks {
  onToken: (text: string) => void;
  onDone: (
    fullText: string,
    emotions: Record<string, number>,
    intent: string,
    mood: MoodState,
    attachment: AttachmentState,
  ) => void;
  onError: (error: string) => void;
  onNoCredits?: (tier: string) => void;
  onSilent?: () => void;
}

/** Split SSE blocks on blank line; return text after last incomplete block. */
function drainSseBlocks(buffer: string, onBlock: (eventName: string, dataRaw: string) => void): string {
  const norm = buffer.replace(/\r\n/g, '\n');
  const parts = norm.split('\n\n');
  const rest = parts.pop() ?? '';
  for (const block of parts) {
    if (!block.trim()) continue;
    let eventName = 'message';
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).startsWith(' ') ? line.slice(6) : line.slice(5));
      }
    }
    if (dataLines.length === 0) continue;
    onBlock(eventName, dataLines.join('\n'));
  }
  return rest;
}

function dispatchSseEvent(eventName: string, dataRaw: string, callbacks: StreamCallbacks): void {
  try {
    const data = JSON.parse(dataRaw);
    if (eventName === 'token') {
      callbacks.onToken(data.text ?? '');
      return;
    }
    if (eventName === 'done') {
      if (data.silent === true) {
        callbacks.onSilent?.();
        return;
      }
      callbacks.onDone(
        data.full_text ?? '',
        data.active_emotions ?? {},
        data.intent ?? '',
        data.mood ?? DEFAULT_MOOD,
        data.attachment ?? DEFAULT_ATTACH,
      );
      return;
    }
    if (eventName === 'no_credits') {
      callbacks.onNoCredits?.(data.tier ?? 'free');
      return;
    }
    if (eventName === 'error') {
      callbacks.onError(data.error ?? 'Stream error');
    }
  } catch {
    // malformed JSON — ignore block
  }
}

/**
 * Stream chat over SSE using POST + JSON body so user text never appears in the URL
 * (avoids proxy logs, analytics, and Referer leaking message content).
 * Returns a cancel function — call it to abort mid-stream.
 */
export function streamMessage(
  sessionId: string,
  text: string,
  callbacks: StreamCallbacks,
): Promise<() => void> {
  const controller = new AbortController();

  void (async () => {
    const token = await getToken();
    const url = `${API_BASE}/api/chat/${sessionId}/message`;

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ text }),
        signal: controller.signal,
      });

      if (!response.ok) {
        let detail = `Request failed (${response.status})`;
        try {
          const errBody = await response.json();
          if (errBody?.detail) detail = String(errBody.detail);
        } catch {
          try {
            const t = await response.text();
            if (t) detail = t.slice(0, 200);
          } catch {
            /* ignore */
          }
        }
        callbacks.onError(detail);
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        callbacks.onError('Streaming is not available on this device');
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (!controller.signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value) buffer += decoder.decode(value, { stream: true });
        buffer = drainSseBlocks(buffer, (ev, raw) => dispatchSseEvent(ev, raw, callbacks));
      }

      if (controller.signal.aborted) return;

      if (buffer.trim()) {
        drainSseBlocks(buffer + '\n\n', (ev, raw) => dispatchSseEvent(ev, raw, callbacks));
      }
    } catch (e) {
      if (controller.signal.aborted) return;
      const msg = e instanceof Error ? e.message : 'Stream error';
      callbacks.onError(msg);
    }
  })();

  return Promise.resolve(() => controller.abort());
}
