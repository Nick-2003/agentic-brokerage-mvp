// SSE client that talks to the backend's POST /api/chat endpoint.
// Yields parsed events as they stream in.

import type { Widget } from './widgets';

export type ChatEvent =
  | { event: 'thought'; data: { text: string } }
  | { event: 'tool_call'; data: { id: string; name: string; args: Record<string, unknown> } }
  | { event: 'tool_result'; data: { id: string; ok: boolean; summary: string } }
  | { event: 'widget'; data: Widget }
  | { event: 'message'; data: { text: string } }
  | { event: 'error'; data: { message: string } }
  | { event: 'done'; data: { elapsed_ms: number; iterations: number } };

export type ChatEventHandler = (event: ChatEvent) => void;

export type ChatRequest = {
  message: string;
  // No user_id — the backend derives identity from the JWT (P4.1).
};

/**
 * Send a chat message and stream the agent's response events.
 *
 * `accessToken` (P4.1) is the Supabase JWT; when present it's sent as
 * `Authorization: Bearer <token>` so the backend can derive the real user_id.
 * Null/undefined → no header → backend uses the "demo" user (REQUIRE_AUTH off).
 *
 * Returns an AbortController so the caller can cancel mid-stream.
 */
export function streamChat(
  req: ChatRequest,
  onEvent: ChatEventHandler,
  apiBase: string = '',
  accessToken?: string | null
): AbortController {
  const ctrl = new AbortController();

  (async () => {
    const url = `${apiBase}/api/chat`;
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;

    let resp: Response;
    try {
      resp = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(req),
        signal: ctrl.signal,
      });
    } catch (err) {
      onEvent({ event: 'error', data: { message: `Network error: ${(err as Error).message}` } });
      onEvent({ event: 'done', data: { elapsed_ms: 0, iterations: 0 } });
      return;
    }

    if (!resp.ok || !resp.body) {
      const detail =
        resp.status === 401
          ? 'Your session has expired — please sign in again.'
          : `Backend returned ${resp.status} ${resp.statusText}`;
      onEvent({ event: 'error', data: { message: detail } });
      onEvent({ event: 'done', data: { elapsed_ms: 0, iterations: 0 } });
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      let value: Uint8Array | undefined;
      let done = false;
      try {
        ({ value, done } = await reader.read());
      } catch (err) {
        if ((err as Error).name === 'AbortError') break;
        onEvent({ event: 'error', data: { message: `Read error: ${(err as Error).message}` } });
        break;
      }
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line. The terminator may be
      // \n\n or \r\n\r\n depending on the server — our backend uses \r\n.
      let sep: RegExpMatchArray | null;
      while ((sep = buffer.match(/\r?\n\r?\n/)) !== null) {
        const frameEnd = sep.index as number;
        const rawFrame = buffer.slice(0, frameEnd);
        buffer = buffer.slice(frameEnd + sep[0].length);
        const parsed = parseFrame(rawFrame);
        if (parsed) onEvent(parsed);
      }
    }

    onEvent({ event: 'done', data: { elapsed_ms: 0, iterations: 0 } });
  })();

  return ctrl;
}

function parseFrame(raw: string): ChatEvent | null {
  let eventName = 'message';
  const dataLines: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    // ignore id:, retry:, comments (lines starting with :)
  }
  const dataStr = dataLines.join('\n');
  if (!dataStr) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(dataStr);
  } catch {
    return null;
  }
  return { event: eventName, data: parsed } as ChatEvent;
}
