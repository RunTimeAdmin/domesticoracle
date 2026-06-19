"use client";

import { useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  getLedger, verifyLedger, getPolicies, addPolicy, deletePolicy,
  getAgents, revokeAgent, restoreAgent, getApprovals, resolveApproval,
  getMode, setMode, getSummary, getDevices, controlDevice,
  LedgerEntry, ChainStatus, Policy, Agent, PendingApproval,
  PolicyMode, LedgerSummary, Device,
} from "@/lib/api";

type Tab = "pending" | "ledger" | "devices" | "policies" | "agents";

interface TrustCenterProps {
  open: boolean;
  onClose: () => void;
  refreshKey: number; // bump to force a reload (e.g. after a new approval arrives)
}

const TABS: { id: Tab; label: string }[] = [
  { id: "pending", label: "Pending" },
  { id: "ledger", label: "Ledger" },
  { id: "devices", label: "Devices" },
  { id: "policies", label: "Policies" },
  { id: "agents", label: "Agents" },
];

export default function TrustCenter({ open, onClose, refreshKey }: TrustCenterProps) {
  const [tab, setTab] = useState<Tab>("pending");
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [chain, setChain] = useState<ChainStatus | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [mode, setModeState] = useState<PolicyMode | null>(null);
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [haLive, setHaLive] = useState(false);
  const [newPolicy, setNewPolicy] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [p, l, c, pol, ag, m, s, dev] = await Promise.all([
        getApprovals(), getLedger(), verifyLedger(), getPolicies(), getAgents(),
        getMode(), getSummary(), getDevices(),
      ]);
      setPending(p);
      setLedger(l);
      setChain(c);
      setPolicies(pol);
      setAgents(ag);
      setModeState(m);
      setSummary(s);
      setDevices(dev.devices);
      setHaLive(dev.configured);
    } catch {
      // backend not reachable; leave panels empty
    }
  }, []);

  const changeMode = async (m: PolicyMode) => {
    setModeState(m); // optimistic
    try {
      await setMode(m);
    } finally {
      reload();
    }
  };

  const control = async (device: string, action: string) => {
    await controlDevice(device, action);
    await reload();
  };

  useEffect(() => {
    if (open) reload();
  }, [open, refreshKey, reload]);

  const submitPolicy = async () => {
    const text = newPolicy.trim();
    if (!text) return;
    setBusy(true);
    try {
      await addPolicy(text);
      setNewPolicy("");
      await reload();
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
            onClick={onClose}
            className="fixed inset-0 z-40 bg-charcoal/20 backdrop-blur-sm"
          />
          <motion.aside
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 300 }}
            className="fixed right-0 top-0 z-50 flex h-[100dvh] w-full max-w-md flex-col bg-cream shadow-2xl"
          >
            <header className="flex items-center justify-between border-b border-rosegold/20 px-5 py-4">
              <div>
                <h2 className="font-serif text-xl font-semibold text-charcoal">
                  Trust Center
                </h2>
                <p className="text-xs text-charcoal-soft">
                  What Ora has done, held, and is allowed to do
                </p>
              </div>
              <button
                onClick={onClose}
                aria-label="Close"
                className="grid h-8 w-8 place-items-center rounded-full text-charcoal-soft hover:bg-charcoal-soft/10"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
              </button>
            </header>

            <nav className="flex gap-1 border-b border-rosegold/20 px-3 py-2">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`relative rounded-full px-3 py-1.5 text-sm font-medium transition ${
                    tab === t.id
                      ? "bg-rosegold/15 text-charcoal"
                      : "text-charcoal-soft hover:bg-charcoal-soft/5"
                  }`}
                >
                  {t.label}
                  {t.id === "pending" && pending.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-rosegold px-1.5 text-xs text-white">
                      {pending.length}
                    </span>
                  )}
                </button>
              ))}
            </nav>

            <div className="flex-1 overflow-y-auto px-5 py-4">
              {tab === "pending" && (
                <PendingTab pending={pending} onResolved={reload} />
              )}
              {tab === "ledger" && (
                <LedgerTab
                  entries={ledger}
                  chain={chain}
                  mode={mode}
                  summary={summary}
                  onChangeMode={changeMode}
                />
              )}
              {tab === "devices" && (
                <DevicesTab devices={devices} live={haLive} onControl={control} />
              )}
              {tab === "policies" && (
                <PoliciesTab
                  policies={policies}
                  newPolicy={newPolicy}
                  setNewPolicy={setNewPolicy}
                  onAdd={submitPolicy}
                  onDelete={async (id) => { await deletePolicy(id); reload(); }}
                  busy={busy}
                />
              )}
              {tab === "agents" && (
                <AgentsTab
                  agents={agents}
                  onRevoke={async (id) => { await revokeAgent(id); reload(); }}
                  onRestore={async (id) => { await restoreAgent(id); reload(); }}
                />
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

function PendingTab({ pending, onResolved }: { pending: PendingApproval[]; onResolved: () => void }) {
  if (pending.length === 0) {
    return <Empty text="Nothing waiting on you. Ora hasn't held anything." />;
  }
  return (
    <div className="space-y-3">
      {pending.map((p) => (
        <div key={p.id} className="rounded-2xl border border-rosegold/30 bg-white/50 p-4">
          <p className="text-sm font-semibold text-charcoal">{p.action}</p>
          <p className="mt-0.5 text-sm text-charcoal">{p.summary}</p>
          <p className="mt-1 text-xs text-charcoal-soft">{p.reason}</p>
          <div className="mt-3 flex gap-2">
            <button
              onClick={async () => { await resolveApproval(p.id, "approve"); onResolved(); }}
              className="flex-1 rounded-full bg-gradient-to-br from-rosegold to-dusty py-1.5 text-sm font-medium text-white"
            >
              Approve
            </button>
            <button
              onClick={async () => { await resolveApproval(p.id, "deny"); onResolved(); }}
              className="flex-1 rounded-full border border-charcoal-soft/30 py-1.5 text-sm font-medium text-charcoal-soft"
            >
              Deny
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

const MODES: { id: PolicyMode; label: string; blurb: string }[] = [
  { id: "audit_only", label: "Audit", blurb: "Watches and logs what it would do. Nothing is blocked." },
  { id: "enforced", label: "Enforced", blurb: "Holds and denies are real. Your policies have teeth." },
  { id: "permissive", label: "Permissive", blurb: "Allows everything, logs lightly." },
];

function ModeControl({
  mode, onChange,
}: {
  mode: PolicyMode | null;
  onChange: (m: PolicyMode) => void;
}) {
  const current = MODES.find((m) => m.id === mode);
  return (
    <div className="rounded-2xl border border-rosegold/30 bg-white/50 p-3">
      <p className="mb-2 text-xs font-medium text-charcoal-soft">Enforcement posture</p>
      <div className="flex gap-1 rounded-full bg-charcoal-soft/5 p-1">
        {MODES.map((m) => (
          <button
            key={m.id}
            onClick={() => onChange(m.id)}
            className={`flex-1 rounded-full px-2 py-1 text-xs font-medium transition ${
              mode === m.id
                ? "bg-gradient-to-br from-rosegold to-dusty text-white shadow"
                : "text-charcoal-soft hover:bg-charcoal-soft/5"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
      {current && <p className="mt-2 text-[11px] text-charcoal-soft">{current.blurb}</p>}
    </div>
  );
}

function SummaryCard({ summary }: { summary: LedgerSummary }) {
  const cats = Object.entries(summary.by_category).sort((a, b) => b[1] - a[1]);
  return (
    <div className="rounded-2xl border border-charcoal-soft/15 bg-white/40 p-3">
      <p className="mb-2 text-xs font-medium text-charcoal-soft">
        Last {summary.window_days} days
      </p>
      <div className="grid grid-cols-3 gap-2 text-center">
        <Stat value={summary.total} label="actions" />
        <Stat value={summary.held} label="held" tone="amber" />
        <Stat value={summary.denied} label="denied" tone="red" />
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-charcoal-soft">
        <span>Avg risk {summary.avg_risk} · peak {summary.max_risk}</span>
        {summary.financial_total > 0 && (
          <span>${summary.financial_total.toFixed(2)} spent</span>
        )}
      </div>
      {cats.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {cats.map(([cat, n]) => (
            <span
              key={cat}
              className="rounded-full bg-charcoal-soft/10 px-2 py-0.5 text-[10px] text-charcoal-soft"
            >
              {cat.replace("_", " ")} · {n}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ value, label, tone }: { value: number; label: string; tone?: "amber" | "red" }) {
  const color =
    tone === "amber" ? "text-amber-600" : tone === "red" ? "text-red-600" : "text-charcoal";
  return (
    <div className="rounded-xl bg-cream/60 py-1.5">
      <p className={`font-serif text-lg font-semibold ${color}`}>{value}</p>
      <p className="text-[10px] text-charcoal-soft">{label}</p>
    </div>
  );
}

function LedgerTab({
  entries, chain, mode, summary, onChangeMode,
}: {
  entries: LedgerEntry[];
  chain: ChainStatus | null;
  mode: PolicyMode | null;
  summary: LedgerSummary | null;
  onChangeMode: (m: PolicyMode) => void;
}) {
  return (
    <div className="space-y-3">
      <ModeControl mode={mode} onChange={onChangeMode} />
      {summary && <SummaryCard summary={summary} />}
      {chain && (
        <div
          className={`flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium ${
            chain.valid
              ? "bg-emerald-50 text-emerald-700"
              : "bg-red-50 text-red-700"
          }`}
        >
          <span className={`h-2 w-2 rounded-full ${chain.valid ? "bg-emerald-500" : "bg-red-500"}`} />
          {chain.valid
            ? `Verified · ${chain.checked} entries, chain intact`
            : `Tampered · break detected at entry #${chain.broken_at}`}
        </div>
      )}
      {entries.length === 0 ? (
        <Empty text="No activity yet." />
      ) : (
        entries.map((e) => (
          <div key={e.id} className="rounded-xl border border-charcoal-soft/15 bg-white/40 p-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="font-medium text-charcoal">{e.action}</span>
              <StatusPill status={e.status} />
            </div>
            <p className="mt-0.5 text-xs text-charcoal-soft">{e.args_summary}</p>
            {e.outcome && <p className="mt-1 text-xs text-charcoal-soft">{e.outcome}</p>}
            <div className="mt-1.5 flex items-center justify-between text-[10px] text-charcoal-soft/70">
              <span>{e.actor_id}</span>
              <span title={e.hash}>#{e.id} · {e.hash.slice(0, 10)}…</span>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function deviceVerbs(domain: string, state: string): { label: string; action: string }[] {
  if (domain === "lock") {
    return state === "locked"
      ? [{ label: "Unlock", action: "unlock" }]
      : [{ label: "Lock", action: "lock" }];
  }
  if (domain === "cover") {
    return state === "open"
      ? [{ label: "Close", action: "close" }]
      : [{ label: "Open", action: "open" }];
  }
  if (["light", "switch", "fan", "input_boolean", "media_player"].includes(domain)) {
    return state === "on"
      ? [{ label: "Turn off", action: "off" }]
      : [{ label: "Turn on", action: "on" }];
  }
  return []; // climate, scene, script: shown read-only for now
}

const ON_STATES = new Set(["on", "open", "unlocked"]);

function DevicesTab({
  devices, live, onControl,
}: {
  devices: Device[];
  live: boolean;
  onControl: (entityId: string, action: string) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="rounded-xl bg-charcoal-soft/5 px-3 py-2 text-[11px] text-charcoal-soft">
        {live
          ? "Connected to your Home Assistant. Actions run through the consent gate."
          : "Showing a mock home (no Home Assistant connected). Actions still flow through the gate."}
      </div>
      {devices.length === 0 ? (
        <Empty text="No devices visible." />
      ) : (
        devices.map((d) => {
          const verbs = deviceVerbs(d.domain, d.state);
          const lit = ON_STATES.has(d.state);
          return (
            <div
              key={d.entity_id}
              className="flex items-center justify-between rounded-xl border border-charcoal-soft/15 bg-white/40 p-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-charcoal">{d.name}</p>
                <p className="mt-0.5 flex items-center gap-1.5 text-[11px] text-charcoal-soft">
                  <span className={`h-1.5 w-1.5 rounded-full ${lit ? "bg-emerald-500" : "bg-charcoal-soft/40"}`} />
                  {d.state}
                  <span className="text-charcoal-soft/50">· {d.entity_id}</span>
                </p>
              </div>
              <div className="flex shrink-0 gap-1.5">
                {verbs.map((v) => (
                  <button
                    key={v.action}
                    onClick={() => onControl(d.entity_id, v.action)}
                    className="rounded-full border border-rosegold/40 px-3 py-1 text-xs font-medium text-charcoal hover:bg-rosegold/10"
                  >
                    {v.label}
                  </button>
                ))}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

function PoliciesTab({
  policies, newPolicy, setNewPolicy, onAdd, onDelete, busy,
}: {
  policies: Policy[];
  newPolicy: string;
  setNewPolicy: (s: string) => void;
  onAdd: () => void;
  onDelete: (id: number) => void;
  busy: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-rosegold/30 bg-white/50 p-3">
        <p className="mb-2 text-xs font-medium text-charcoal-soft">
          Add a rule in plain language
        </p>
        <input
          value={newPolicy}
          onChange={(e) => setNewPolicy(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onAdd()}
          placeholder="e.g. never spend more than $100"
          className="w-full rounded-lg border border-charcoal-soft/20 bg-cream px-3 py-2 text-sm text-charcoal focus:outline-none focus:ring-1 focus:ring-rosegold"
        />
        <button
          onClick={onAdd}
          disabled={busy || !newPolicy.trim()}
          className="mt-2 w-full rounded-full bg-gradient-to-br from-rosegold to-dusty py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "Adding…" : "Add policy"}
        </button>
      </div>
      {policies.map((p) => (
        <div key={p.id} className="flex items-start justify-between gap-2 rounded-xl border border-charcoal-soft/15 bg-white/40 p-3">
          <div>
            <p className="text-sm text-charcoal">{p.label || p.rule_type}</p>
            <p className="mt-0.5 text-[10px] uppercase tracking-wide text-charcoal-soft/70">
              {p.rule_type}{p.source === "default" ? " · default" : ""}
            </p>
          </div>
          <button
            onClick={() => onDelete(p.id)}
            aria-label="Delete policy"
            className="shrink-0 rounded-full p-1 text-charcoal-soft hover:bg-red-50 hover:text-red-600"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>
          </button>
        </div>
      ))}
    </div>
  );
}

function AgentsTab({
  agents, onRevoke, onRestore,
}: {
  agents: Agent[];
  onRevoke: (id: string) => void;
  onRestore: (id: string) => void;
}) {
  return (
    <div className="space-y-3">
      {agents.map((a) => (
        <div key={a.id} className="flex items-center justify-between rounded-xl border border-charcoal-soft/15 bg-white/40 p-3">
          <div>
            <p className="text-sm font-medium text-charcoal">{a.name}</p>
            <p className="text-[10px] text-charcoal-soft/70">{a.id}</p>
          </div>
          {a.status === "active" ? (
            <button
              onClick={() => onRevoke(a.id)}
              className="rounded-full border border-red-200 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
            >
              Revoke
            </button>
          ) : (
            <button
              onClick={() => onRestore(a.id)}
              className="rounded-full border border-charcoal-soft/30 px-3 py-1 text-xs font-medium text-charcoal-soft hover:bg-charcoal-soft/5"
            >
              Restore
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "executed" ? "bg-emerald-100 text-emerald-700"
    : status === "pending" ? "bg-amber-100 text-amber-700"
    : status === "blocked" || status === "denied" ? "bg-red-100 text-red-700"
    : "bg-charcoal-soft/10 text-charcoal-soft";
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${tone}`}>{status}</span>;
}

function Empty({ text }: { text: string }) {
  return <p className="mt-8 text-center text-sm text-charcoal-soft">{text}</p>;
}
