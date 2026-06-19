"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { resolveApproval, PendingApproval } from "@/lib/api";

interface ApprovalCardProps {
  approval: {
    id: string;
    action: string;
    summary: string;
    reason: string;
    ledger_id?: number;
  };
  onResolved?: (id: string, decision: "approve" | "deny") => void;
}

const ACTION_LABELS: Record<string, string> = {
  make_purchase: "Purchase",
  send_message: "Message",
  control_device: "Device control",
};

export default function ApprovalCard({ approval, onResolved }: ApprovalCardProps) {
  const [state, setState] = useState<"idle" | "working" | "approve" | "deny">("idle");

  const decide = async (decision: "approve" | "deny") => {
    setState("working");
    try {
      await resolveApproval(approval.id, decision);
      setState(decision);
      onResolved?.(approval.id, decision);
    } catch {
      setState("idle");
    }
  };

  const label = ACTION_LABELS[approval.action] ?? approval.action;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass mx-auto w-full max-w-md rounded-2xl border border-rosegold/40 p-4 shadow-warm"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-full bg-rosegold/20 text-rosegold">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
        </span>
        <div>
          <p className="text-sm font-semibold text-charcoal">Awaiting your okay</p>
          <p className="text-xs text-charcoal-soft">
            {label} · held by Ora
          </p>
        </div>
      </div>

      <p className="mb-1 text-sm text-charcoal">{approval.summary}</p>
      <p className="mb-3 text-xs text-charcoal-soft">{approval.reason}</p>

      {state === "idle" || state === "working" ? (
        <div className="flex gap-2">
          <button
            disabled={state === "working"}
            onClick={() => decide("approve")}
            className="flex-1 rounded-full bg-gradient-to-br from-rosegold to-dusty py-2 text-sm font-medium text-white shadow-soft transition hover:brightness-105 active:scale-95 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            disabled={state === "working"}
            onClick={() => decide("deny")}
            className="flex-1 rounded-full border border-charcoal-soft/30 py-2 text-sm font-medium text-charcoal-soft transition hover:bg-charcoal-soft/5 active:scale-95 disabled:opacity-50"
          >
            Deny
          </button>
        </div>
      ) : (
        <p
          className={`text-sm font-medium ${
            state === "approve" ? "text-emerald-600" : "text-charcoal-soft"
          }`}
        >
          {state === "approve" ? "Approved and carried out." : "Denied. Nothing happened."}
        </p>
      )}
    </motion.div>
  );
}

export type { PendingApproval };
