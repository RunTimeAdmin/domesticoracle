"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ChatWindow from "@/components/ChatWindow";
import ChatInput from "@/components/ChatInput";
import OraAvatar from "@/components/OraAvatar";
import TrustCenter from "@/components/TrustCenter";
import { Message } from "@/components/MessageBubble";
import { streamChat, getUserId, ApprovalEvent } from "@/lib/stream";

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [userId, setUserId] = useState("default");
  const [trustOpen, setTrustOpen] = useState(false);
  const [trustRefresh, setTrustRefresh] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setUserId(getUserId());
  }, []);

  const handleSend = useCallback(
    (text: string) => {
      if (streamingId) return;

      const userMsg: Message = { id: `u_${Date.now()}`, role: "user", content: text };
      const oraId = `o_${Date.now()}`;
      const oraMsg: Message = { id: oraId, role: "ora", content: "" };

      setMessages((prev) => [...prev, userMsg, oraMsg]);
      setStreamingId(oraId);

      const controller = new AbortController();
      abortRef.current = controller;

      streamChat(
        userId,
        text,
        {
          onText: (delta) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === oraId ? { ...m, content: m.content + delta } : m
              )
            );
          },
          onApproval: (_approval: ApprovalEvent) => {
            // A guarded action was held. Nudge the Trust Center to refresh and badge it.
            setPendingCount((c) => c + 1);
            setTrustRefresh((k) => k + 1);
          },
          onDone: () => {
            setStreamingId(null);
            abortRef.current = null;
            setTrustRefresh((k) => k + 1);
          },
          onError: (msg) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === oraId
                  ? { ...m, content: m.content || `I couldn't reach myself just now. ${msg}` }
                  : m
              )
            );
            setStreamingId(null);
            abortRef.current = null;
          },
        },
        controller.signal
      );
    },
    [streamingId, userId]
  );

  return (
    <main className="mx-auto flex h-[100dvh] max-w-4xl flex-col">
      <header className="flex items-center justify-between px-6 py-4 sm:px-8">
        <div className="flex items-center gap-3">
          <OraAvatar size={40} speaking={!!streamingId} />
          <div>
            <p className="font-serif text-xl font-semibold leading-none text-charcoal">
              Ora
            </p>
            <p className="text-xs text-charcoal-soft">Domestic Oracle</p>
          </div>
        </div>

        <button
          onClick={() => { setTrustOpen(true); setPendingCount(0); }}
          className="relative flex items-center gap-2 rounded-full border border-rosegold/30 bg-white/40 px-4 py-2 text-sm font-medium text-charcoal shadow-soft transition hover:bg-white/70"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
          Trust Center
          {pendingCount > 0 && (
            <span className="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full bg-rosegold px-1 text-xs text-white">
              {pendingCount}
            </span>
          )}
        </button>
      </header>

      <ChatWindow messages={messages} streamingId={streamingId} />
      <ChatInput onSend={handleSend} disabled={!!streamingId} />

      <TrustCenter
        open={trustOpen}
        onClose={() => setTrustOpen(false)}
        refreshKey={trustRefresh}
      />
    </main>
  );
}
