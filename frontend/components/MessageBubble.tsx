"use client";

import { motion } from "framer-motion";
import OraAvatar from "./OraAvatar";

export interface Message {
  id: string;
  role: "user" | "ora";
  content: string;
}

interface MessageBubbleProps {
  message: Message;
  streaming?: boolean;
}

export default function MessageBubble({ message, streaming }: MessageBubbleProps) {
  const isOra = message.role === "ora";

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className={`flex w-full items-end gap-3 ${
        isOra ? "justify-start" : "justify-end"
      }`}
    >
      {isOra && (
        <div className="mb-1 shrink-0">
          <OraAvatar size={36} speaking={streaming} />
        </div>
      )}

      <div
        className={`max-w-[78%] rounded-3xl px-5 py-3 text-[15px] leading-relaxed shadow-soft ${
          isOra
            ? "glass rounded-bl-lg text-charcoal"
            : "rounded-br-lg bg-gradient-to-br from-rosegold to-dusty text-white"
        }`}
      >
        <p className="whitespace-pre-wrap">
          {message.content}
          {streaming && (
            <motion.span
              className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 bg-rosegold"
              animate={{ opacity: [1, 0.2, 1] }}
              transition={{ duration: 1, repeat: Infinity }}
            />
          )}
        </p>
      </div>
    </motion.div>
  );
}
