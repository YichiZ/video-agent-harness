import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatTurn, ServerFrame } from "../protocol";

const RECONNECT_MS = 1000;

export interface ChatState {
  turns: ChatTurn[];
  running: boolean;
  connected: boolean;
  error: string | null;
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

/** The `WS /chat` connection: history on connect, streamed replies, Stop. Reconnects. */
export function useChat() {
  const [state, setState] = useState<ChatState>({ turns: [], running: false, connected: false, error: null });
  const socket = useRef<WebSocket | null>(null);
  const generation = useRef<number | null>(null); // the upload the shown chat belongs to

  useEffect(() => {
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${scheme}://${location.host}/chat`);
      socket.current = ws;
      ws.onmessage = (event) => {
        const frame = JSON.parse(event.data) as ServerFrame;
        if (frame.type === "history") {
          // Connected only now, so a message sent before this can't be overwritten by it.
          generation.current = frame.generation;
          setState((s) => ({ ...s, turns: frame.turns, running: false, error: null, connected: true }));
        } else if (frame.type === "ingest_status") {
          if (generation.current !== null && frame.generation !== generation.current) {
            generation.current = frame.generation; // a new upload cleared the chat on the server
            setState((s) => ({ ...s, turns: [], running: false, error: null }));
          }
        } else if (frame.type === "error") {
          setState((s) =>
            frame.refused
              ? { ...s, error: frame.message, running: false, turns: dropUnanswered(s.turns) }
              : { ...s, error: frame.message, running: false }, // the turn failed; keep what came
          );
        } else if (frame.type === "result") {
          setState((s) => ({ ...s, turns: applyFrame(s.turns, frame), running: false }));
        } else {
          setState((s) => ({ ...s, turns: applyFrame(s.turns, frame) }));
        }
      };
      ws.onclose = () => {
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
  }, []);

  const send = useCallback((text: string) => {
    const ws = socket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
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
  }, []);

  const stop = useCallback(() => {
    socket.current?.send(JSON.stringify({ type: "interrupt" }));
  }, []);

  return { ...state, send, stop };
}
