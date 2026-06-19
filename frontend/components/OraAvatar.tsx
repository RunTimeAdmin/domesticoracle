"use client";

import { motion } from "framer-motion";

interface OraAvatarProps {
  size?: number;
  speaking?: boolean;
}

// A soft, glowing orb that gently breathes - Ora's visual presence.
// It pulses a little faster while she's composing a reply.
export default function OraAvatar({ size = 64, speaking = false }: OraAvatarProps) {
  return (
    <div
      className="relative flex items-center justify-center"
      style={{ width: size, height: size }}
    >
      <motion.span
        className="absolute inset-0 rounded-full blur-md"
        style={{
          background:
            "radial-gradient(circle at 35% 30%, #E8CFCF 0%, #D4A5A5 45%, #C9A96E 100%)",
        }}
        animate={{
          scale: speaking ? [1, 1.18, 1] : [1, 1.06, 1],
          opacity: speaking ? [0.85, 1, 0.85] : [0.7, 0.9, 0.7],
        }}
        transition={{
          duration: speaking ? 1.4 : 4,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />
      <motion.span
        className="relative rounded-full shadow-warm"
        style={{
          width: size * 0.62,
          height: size * 0.62,
          background:
            "radial-gradient(circle at 35% 30%, #FFFFFF 0%, #E8CFCF 40%, #C9A96E 100%)",
        }}
        animate={{ scale: speaking ? [1, 1.08, 1] : 1 }}
        transition={{
          duration: 1.2,
          repeat: speaking ? Infinity : 0,
          ease: "easeInOut",
        }}
      />
    </div>
  );
}
