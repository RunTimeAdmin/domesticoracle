"use client";

import { useEffect, useRef } from "react";
import { AnimatePresence } from "framer-motion";
import MessageBubble, { Message } from "./MessageBubble";
import OraAvatar from "./OraAvatar";

interface ChatWindowProps {
  messages: Message[];
  streamingId: string | null;
}

export default function ChatWindow({ messages, streamingId }: ChatWindowProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    });
    return () => cancelAnimationFrame(id);
  }, [messages, streamingId]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 text-center">
        <OraAvatar size={108} />
        <div className="space-y-2">
          <h1 className="font-serif text-4xl font-semibold text-charcoal">
            Hello, I&apos;m Ora.
          </h1>
          <p className="max-w-md text-charcoal-soft">
            Your home&apos;s oracle, and its quiet guardian. Talk to me, let me
            look things up, or ask me to handle something. Anything that spends,
            messages, or changes your home passes through me first.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-5 overflow-y-auto px-4 py-6 sm:px-8">
      <AnimatePresence initial={false}>
        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            message={m}
            streaming={m.id === streamingId}
          />
        ))}
      </AnimatePresence>
      <div ref={bottomRef} />
    </div>
  );
}
