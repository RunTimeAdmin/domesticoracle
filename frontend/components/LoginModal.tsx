"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { login } from "@/lib/api";

interface LoginModalProps {
  open: boolean;
  onSuccess: () => void;
}

export default function LoginModal({ open, onSuccess }: LoginModalProps) {
  const [passphrase, setPassphrase] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setPassphrase("");
      setError("");
      setTimeout(() => inputRef.current?.focus(), 80);
    }
  }, [open]);

  const submit = async () => {
    if (!passphrase.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const ok = await login(passphrase.trim());
      if (ok) {
        onSuccess();
      } else {
        setError("Wrong passphrase.");
        setPassphrase("");
        inputRef.current?.focus();
      }
    } catch {
      setError("Couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-charcoal/30 backdrop-blur-sm"
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }}
            transition={{ type: "spring", damping: 28, stiffness: 320 }}
            className="fixed inset-0 z-50 flex items-center justify-center px-4"
          >
            <div className="w-full max-w-sm rounded-3xl bg-cream p-8 shadow-2xl">
              <div className="mb-6 flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-full bg-rosegold/15">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-rosegold">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                  </svg>
                </div>
                <div>
                  <p className="font-serif text-lg font-semibold leading-none text-charcoal">
                    Domestic Oracle
                  </p>
                  <p className="text-xs text-charcoal-soft">Owner access required</p>
                </div>
              </div>

              <input
                ref={inputRef}
                type="password"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submit()}
                placeholder="Owner passphrase"
                autoComplete="current-password"
                className="w-full rounded-xl border border-charcoal-soft/20 bg-white/60 px-4 py-3 text-sm text-charcoal placeholder-charcoal-soft/50 focus:outline-none focus:ring-2 focus:ring-rosegold/40"
              />

              {error && (
                <p className="mt-2 text-xs text-red-500">{error}</p>
              )}

              <button
                onClick={submit}
                disabled={busy || !passphrase.trim()}
                className="mt-4 w-full rounded-full bg-gradient-to-br from-rosegold to-dusty py-3 text-sm font-semibold text-white shadow-soft transition hover:opacity-90 disabled:opacity-50"
              >
                {busy ? "Verifying…" : "Unlock"}
              </button>

              <p className="mt-4 text-center text-[11px] text-charcoal-soft/60">
                Your passphrase is never stored in the browser.
              </p>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
