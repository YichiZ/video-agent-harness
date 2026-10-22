import { useEffect, useState } from "react";
import { type ChatSummary, deleteChat, listChats, renameChat } from "../api";

interface Props {
  videoId: string | null;
  current: string | null;
  /** Bumped whenever the list may have changed (a turn ended, a title arrived). */
  version: number;
  disabled: boolean;
  /** Open a chat: an id, "new", or null for the most recent one. */
  onOpen: (chat: string | null) => void;
}

/** The video's chats, most recent first: switch, start, rename and delete. */
export default function ChatList({ videoId, current, version, disabled, onOpen }: Props) {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [refreshes, setRefreshes] = useState(0);

  useEffect(() => {
    if (!videoId) {
      setChats([]);
      return;
    }
    let live = true;
    listChats().then(
      (found) => live && setChats(found),
      (e) => console.error(e),
    );
    return () => {
      live = false;
    };
  }, [videoId, version, refreshes]);

  async function act(action: () => Promise<void>) {
    try {
      await action();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : String(e));
    }
    setRefreshes((n) => n + 1);
  }

  function rename(chat: ChatSummary) {
    const title = window.prompt("Rename chat", chat.title)?.trim();
    if (title && title !== chat.title) act(() => renameChat(chat.id, title));
  }

  function remove(chat: ChatSummary) {
    if (!window.confirm(`Delete "${chat.title}"?`)) return;
    act(async () => {
      await deleteChat(chat.id);
      if (chat.id === current) onOpen(null);
    });
  }

  if (!videoId) return null;
  return (
    <nav aria-label="chats" className="border-b border-neutral-800 p-2 text-sm">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-neutral-500">Chats</span>
        <button
          type="button"
          onClick={() => onOpen("new")}
          disabled={disabled}
          className="rounded bg-neutral-800 px-2 py-0.5 text-xs hover:bg-neutral-700 disabled:opacity-50"
        >
          New chat
        </button>
      </div>
      <ul className="max-h-32 space-y-0.5 overflow-y-auto">
        {chats.map((chat) => (
          <li
            key={chat.id}
            data-chat={chat.id}
            aria-current={chat.id === current ? "true" : undefined}
            className={`group flex items-center gap-1 rounded px-1 ${chat.id === current ? "bg-neutral-800" : "hover:bg-neutral-900"}`}
          >
            <button
              type="button"
              onClick={() => onOpen(chat.id)}
              disabled={disabled || chat.id === current}
              className="flex-1 truncate py-0.5 text-left disabled:cursor-default"
            >
              {chat.title}
            </button>
            <button
              type="button"
              aria-label={`Rename ${chat.title}`}
              onClick={() => rename(chat)}
              disabled={disabled}
              className="text-xs text-neutral-500 hover:text-neutral-200 disabled:opacity-50"
            >
              ✎
            </button>
            <button
              type="button"
              aria-label={`Delete ${chat.title}`}
              onClick={() => remove(chat)}
              disabled={disabled}
              className="text-xs text-neutral-500 hover:text-rose-400 disabled:opacity-50"
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
