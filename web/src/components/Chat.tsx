import { type FormEvent, useEffect, useRef, useState } from "react";
import { parseChips } from "../lib/chips";
import { useChat } from "../lib/useChat";
import type { ChatTurn, ToolCall } from "../protocol";

const PIN_PX = 40; // this close to the bottom counts as "at the bottom"

interface Props {
  videoId: string | null;
  chatReady: boolean;
  onSeek: (t: number) => void;
}

function AgentText({ text, onSeek }: { text: string; onSeek: (t: number) => void }) {
  return (
    <p className="whitespace-pre-wrap">
      {parseChips(text).map((part, i) =>
        part.kind === "text" ? (
          <span key={i}>{part.text}</span>
        ) : (
          <button
            key={i}
            type="button"
            data-chip={part.start}
            onClick={() => onSeek(part.start)}
            className="mx-0.5 rounded bg-sky-900 px-1 font-mono text-xs text-sky-100 hover:bg-sky-700"
          >
            {part.label.slice(1, -1)}
          </button>
        ),
      )}
    </p>
  );
}

function ToolRow({ tool }: { tool: ToolCall }) {
  return (
    <details data-tool={tool.name} className="my-1 rounded bg-neutral-900 px-2 py-1 text-xs text-neutral-400">
      <summary className="cursor-pointer">
        {tool.name}
        {tool.result === null ? " …" : tool.is_error ? " (error)" : ""}
      </summary>
      <pre data-tool-input className="mt-1 overflow-x-auto whitespace-pre-wrap">
        {JSON.stringify(tool.input, null, 2)}
      </pre>
      {tool.result !== null && (
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap border-t border-neutral-800 pt-1">
          {tool.result}
        </pre>
      )}
    </details>
  );
}

function Turn({ turn, onSeek }: { turn: ChatTurn; onSeek: (t: number) => void }) {
  if (turn.role === "user") {
    return (
      <div data-role="user" className="ml-8 rounded-lg bg-neutral-800 px-3 py-2">
        {turn.text}
      </div>
    );
  }
  return (
    <div data-role="assistant" className="mr-4">
      {turn.tools.map((tool, i) => (
        <ToolRow key={tool.id ?? i} tool={tool} />
      ))}
      <AgentText text={turn.text} onSeek={onSeek} />
      {turn.interrupted && <p className="text-xs text-neutral-500">(stopped)</p>}
    </div>
  );
}

export default function Chat({ videoId, chatReady, onSeek }: Props) {
  const chat = useChat();
  const [draft, setDraft] = useState("");
  const list = useRef<HTMLDivElement>(null);
  const pinned = useRef(true); // follow new text only while the reader is at the bottom
  const last = chat.turns[chat.turns.length - 1];

  useEffect(() => {
    const el = list.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [chat.turns.length, last?.text]);

  function onScroll() {
    const el = list.current!;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < PIN_PX;
  }

  const canSend = chatReady && chat.connected && !chat.running;
  function submit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !canSend) return;
    pinned.current = true;
    chat.send(text);
    setDraft("");
  }

  return (
    <aside className="flex min-h-0 flex-col rounded-lg border border-neutral-800" aria-label="chat">
      <div ref={list} onScroll={onScroll} className="flex-1 space-y-3 overflow-y-auto p-3 text-sm">
        {chat.turns.length === 0 && (
          <p className="text-neutral-500">
            {!videoId
              ? "Upload a video to start."
              : chatReady
                ? "Ask anything about the video."
                : "Chat opens once the transcript and overview are ready."}
          </p>
        )}
        {chat.turns.map((turn, i) => (
          <Turn key={i} turn={turn} onSeek={onSeek} />
        ))}
        {chat.error && <p className="text-rose-400" role="alert">{chat.error}</p>}
      </div>
      <form onSubmit={submit} className="flex gap-2 border-t border-neutral-800 p-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={!canSend}
          placeholder={chatReady ? "Message…" : "Waiting for the video…"}
          aria-label="message"
          className="flex-1 rounded bg-neutral-900 px-2 py-1 disabled:opacity-50"
        />
        {chat.running ? (
          <button type="button" onClick={chat.stop} className="rounded bg-rose-800 px-3 py-1 hover:bg-rose-700">
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!canSend || !draft.trim()} className="rounded bg-sky-800 px-3 py-1 hover:bg-sky-700 disabled:opacity-50">
            Send
          </button>
        )}
      </form>
    </aside>
  );
}
