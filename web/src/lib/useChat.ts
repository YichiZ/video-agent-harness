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

/** A refused message (not ready, turn running) leaves an empty reply: take the pair back out. */
function dropUnanswered(turns: ChatTurn[]): ChatTurn[] {
  const last = turns[turns.length - 1];
  return last?.role === "assistant" && !last.text && last.tools.length === 0 ? turns.slice(0, -2) : turns;
}

/** The `WS /chat` connection: history on connect, streamed replies, Stop. Reconnects. */
export function useChat(videoId: string | null) {
  const [state, setState] = useState<ChatState>({ turns: [], running: false, connected: false, error: null });
  const socket = useRef<WebSocket | null>(null);
  const historyVideo = useRef<string | null>(null);

  useEffect(() => {
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${scheme}://${location.host}/chat`);
      socket.current = ws;
      ws.onopen = () => setState((s) => ({ ...s, connected: true }));
      ws.onmessage = (event) => {
        const frame = JSON.parse(event.data) as ServerFrame;
        if (frame.type === "history") {
          historyVideo.current = frame.video_id;
          setState((s) => ({ ...s, turns: frame.turns, running: false, error: null }));
        } else if (frame.type === "error") {
          setState((s) => ({ ...s, error: frame.message, running: false, turns: dropUnanswered(s.turns) }));
        } else if (frame.type === "result") {
          setState((s) => ({ ...s, turns: applyFrame(s.turns, frame), running: false }));
        } else if (frame.type !== "ingest_status") {
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

  // A new upload clears the chat on the server; clear it here too.
  useEffect(() => {
    if (videoId && historyVideo.current !== null && videoId !== historyVideo.current) {
      historyVideo.current = videoId;
      setState((s) => ({ ...s, turns: [], running: false, error: null }));
    }
  }, [videoId]);

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
