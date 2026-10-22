import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatTurn, ServerFrame } from "../protocol";

const RECONNECT_MS = 1000;

export interface ChatState {
  chatId: string | null;
  turns: ChatTurn[];
  running: boolean;
  connected: boolean;
  error: string | null;
  /** Bumped when the chat list may have changed: a chat opened, a turn ended, a title came. */
  version: number;
}

/** Apply one server frame to the turns; the last turn is the assistant's reply in progress. */
function applyFrame(turns: ChatTurn[], frame: ServerFrame): ChatTurn[] {
  const last = turns[turns.length - 1];
  if (!last || last.role !== "assistant") return turns;
  const replace = (next: ChatTurn) => [...turns.slice(0, -1), next];
  switch (frame.type) {
    case "text_delta":
      return replace({ ...last, text: last.text + frame.text });
    case "tool_use":
      return replace({
        ...last,
        tools: [...last.tools, { id: frame.id, name: frame.name, input: frame.input, result: null, is_error: false }],
      });
    case "tool_result":
      return replace({
        ...last,
        tools: last.tools.map((t) =>
          t.id === frame.id ? { ...t, result: frame.text, is_error: frame.is_error } : t,
        ),
      });
    case "result":
      return replace({ ...last, interrupted: frame.interrupted });
    default:
      return turns;
  }
}

/** A refused message (not ready, a turn already running) gets no reply: take the pair back out. */
function dropUnanswered(turns: ChatTurn[]): ChatTurn[] {
  const last = turns[turns.length - 1];
  return last?.role === "assistant" && !last.text && last.tools.length === 0 ? turns.slice(0, -2) : turns;
}

/**
 * The `WS /chat` connection to one chat: history on connect, streamed replies, Stop.
 * Reconnects to the same chat; `open()` switches chats, and a new upload reopens
 * the new video's most recent chat.
 */
export function useChat() {
  const [state, setState] = useState<ChatState>({
    chatId: null,
    turns: [],
    running: false,
    connected: false,
    error: null,
    version: 0,
  });
  const [target, setTarget] = useState<{ chat: string | null; n: number }>({ chat: null, n: 0 });
  const socket = useRef<WebSocket | null>(null);
  const generation = useRef<number | null>(null); // the upload the shown chat belongs to

  useEffect(() => {
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let want = target.chat; // the chat to (re)connect to: an id, "new", or null for the latest
    setState((s) => ({ ...s, connected: false, running: false, error: null }));

    function connect() {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      const query = want ? `?chat=${encodeURIComponent(want)}` : "";
      const ws = new WebSocket(`${scheme}://${location.host}/chat${query}`);
      socket.current = ws;
      ws.onmessage = (event) => {
        const frame = JSON.parse(event.data) as ServerFrame;
        if (frame.type === "history") {
          // Connected only now, so a message sent before this can't be overwritten by it.
          generation.current = frame.generation;
          want = frame.chat_id;
          setState((s) => ({
            ...s,
            chatId: frame.chat_id,
            turns: frame.turns,
            running: false,
            error: null,
            connected: true,
            version: s.version + 1,
          }));
        } else if (frame.type === "ingest_status") {
          if (generation.current !== null && frame.generation !== generation.current) {
            generation.current = frame.generation; // a new upload: open that video's chats
            want = null;
            ws.onclose = null;
            ws.close();
            setState((s) => ({ ...s, chatId: null, turns: [], running: false, error: null, connected: false }));
            connect();
          }
        } else if (frame.type === "chat_title") {
          setState((s) => ({ ...s, version: s.version + 1 }));
        } else if (frame.type === "error") {
          setState((s) =>
            frame.refused
              ? { ...s, error: frame.message, running: false, turns: dropUnanswered(s.turns) }
              : { ...s, error: frame.message, running: false }, // the turn failed; keep what came
          );
        } else if (frame.type === "result") {
          setState((s) => ({ ...s, turns: applyFrame(s.turns, frame), running: false, version: s.version + 1 }));
        } else {
          setState((s) => ({ ...s, turns: applyFrame(s.turns, frame) }));
        }
      };
      ws.onclose = () => {
        if (socket.current !== ws) return; // an old chat's socket, closed after switching
        setState((s) => ({ ...s, connected: false, running: false }));
        if (!closed) timer = setTimeout(connect, RECONNECT_MS);
      };
    }

    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      socket.current?.close();
    };
  }, [target]);

  /** Switch to a chat: an id, "new", or null for the most recent one. */
  const open = useCallback((chat: string | null) => setTarget((t) => ({ chat, n: t.n + 1 })), []);

  /** Send a message; false if the socket isn't open (switching chats, reconnecting). */
  const send = useCallback((text: string) => {
    const ws = socket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ type: "user_message", text }));
    setState((s) => ({
      ...s,
      running: true,
      error: null,
      turns: [
        ...s.turns,
        { role: "user", text, tools: [], interrupted: false },
        { role: "assistant", text: "", tools: [], interrupted: false },
      ],
    }));
    return true;
  }, []);

  const stop = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: "interrupt" }));
  }, []);

  return { ...state, send, stop, open };
}
