"use client";

import { useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  getLedger, verifyLedger, getPolicies, addPolicy, deletePolicy,
  getAgents, revokeAgent, restoreAgent, getApprovals, resolveApproval,
  getMode, setMode, getSummary, getDevices, controlDevice, dryrunPolicy,
  getLimitsStatus, getKeysStatus, rotateKey, backupKey,
  LedgerEntry, ChainStatus, Policy, Agent, PendingApproval,
  PolicyMode, LedgerSummary, Device, DryRunResult, LimitsStatus,
  KeysStatus, KeyBackup,
} from "@/lib/api";

type Tab = "pending" | "ledger" | "devices" | "policies" | "agents" | "keys";

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
  { id: "keys", label: "Keys" },
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
  const [dryRunResult, setDryRunResult] = useState<DryRunResult | null>(null);
  const [dryRunBusy, setDryRunBusy] = useState(false);

  const [limitsStatus, setLimitsStatus] = useState<LimitsStatus | null>(null);
  const [keysStatus, setKeysStatus] = useState<KeysStatus | null>(null);
  const [verifying, setVerifying] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [p, l, pol, ag, m, s, dev, lim, ks] = await Promise.all([
        getApprovals(), getLedger(), getPolicies(), getAgents(),
        getMode(), getSummary(), getDevices(), getLimitsStatus(), getKeysStatus(),
      ]);
      setPending(p);
      setLedger(l);
      setPolicies(pol);
      setAgents(ag);
      setModeState(m);
      setSummary(s);
      setDevices(dev.devices);
      setHaLive(dev.configured);
      setLimitsStatus(lim);
      setKeysStatus(ks);
    } catch {
      // backend not reachable; leave panels empty
    }
  }, []);

  const verifyChain = useCallback(async () => {
    setVerifying(true);
    try {
      const c = await verifyLedger();
      setChain(c);
    } catch {
      // ignore
    } finally {
      setVerifying(false);
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
    if (!open) return;
    const t = setTimeout(reload, 300);
    return () => clearTimeout(t);
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
                className="grid h-10 w-10 place-items-center rounded-full text-charcoal-soft hover:bg-charcoal-soft/10 sm:h-8 sm:w-8"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
              </button>
            </header>

            <nav className="flex gap-1 overflow-x-auto border-b border-rosegold/20 px-3 py-2 [&::-webkit-scrollbar]:hidden [scrollbar-width:none]">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`relative shrink-0 rounded-full px-2.5 py-1.5 text-xs font-medium transition sm:px-3 sm:text-sm ${
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

            <div className="flex-1 overflow-y-auto px-5 py-4 pb-8">
              {tab === "pending" && (
                <PendingTab pending={pending} onResolved={reload} />
              )}
              {tab === "ledger" && (
                <LedgerTab
                  entries={ledger}
                  chain={chain}
                  mode={mode}
                  summary={summary}
                  limitsStatus={limitsStatus}
                  onChangeMode={changeMode}
                  onVerify={verifyChain}
                  verifying={verifying}
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
                  dryRunResult={dryRunResult}
                  dryRunBusy={dryRunBusy}
                  onDryRun={async (action, args) => {
                    setDryRunBusy(true);
                    try {
                      setDryRunResult(await dryrunPolicy(action, args));
                    } finally {
                      setDryRunBusy(false);
                    }
                  }}
                />
              )}
              {tab === "agents" && (
                <AgentsTab
                  agents={agents}
                  onRevoke={async (id) => { await revokeAgent(id); reload(); }}
                  onRestore={async (id) => { await restoreAgent(id); reload(); }}
                />
              )}
              {tab === "keys" && (
                <KeysTab status={keysStatus} onRotated={reload} />
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
              className="flex-1 rounded-full bg-gradient-to-br from-rosegold to-dusty py-3 text-sm font-medium text-white"
            >
              Approve
            </button>
            <button
              onClick={async () => { await resolveApproval(p.id, "deny"); onResolved(); }}
              className="flex-1 rounded-full border border-charcoal-soft/30 py-3 text-sm font-medium text-charcoal-soft"
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

function LimitsCard({ status }: { status: LimitsStatus }) {
  const dailyPct = status.daily_cap > 0 ? status.daily_count / status.daily_cap : 0;
  const barColor =
    dailyPct >= 1 ? "bg-red-500"
    : dailyPct >= 0.8 ? "bg-amber-500"
    : "bg-emerald-500";
  const textColor =
    dailyPct >= 1 ? "text-red-700"
    : dailyPct >= 0.8 ? "text-amber-700"
    : "text-charcoal-soft";

  const actorEntries = Object.entries(status.actor_counts_this_hour);

  return (
    <div className="rounded-2xl border border-charcoal-soft/15 bg-white/40 p-3">
      <p className="mb-2 text-xs font-medium text-charcoal-soft">Blast-radius limits</p>

      {/* Daily cap bar */}
      <div className="mb-3">
        <div className="mb-1 flex items-center justify-between text-[11px]">
          <span className="text-charcoal-soft">Today's actions</span>
          <span className={`font-medium ${textColor}`}>
            {status.daily_count} / {status.daily_cap > 0 ? status.daily_cap : "∞"}
          </span>
        </div>
        {status.daily_cap > 0 && (
          <div className="h-1.5 overflow-hidden rounded-full bg-charcoal-soft/10">
            <div
              className={`h-full rounded-full transition-all ${barColor}`}
              style={{ width: `${Math.min(100, dailyPct * 100).toFixed(1)}%` }}
            />
          </div>
        )}
        {dailyPct >= 1 && (
          <p className="mt-1 text-[10px] text-red-600">
            Daily cap reached — all actions held until midnight UTC.
          </p>
        )}
      </div>

      {/* Per-actor this hour */}
      {actorEntries.length > 0 && (
        <div>
          <p className="mb-1 text-[10px] text-charcoal-soft/70 uppercase tracking-wide">
            This hour
            {status.actor_hourly_limit > 0 && ` (limit: ${status.actor_hourly_limit}/actor)`}
          </p>
          <div className="space-y-1">
            {actorEntries.map(([actor, count]) => {
              const actorPct = status.actor_hourly_limit > 0
                ? count / status.actor_hourly_limit : 0;
              const hit = status.actor_hourly_limit > 0 && count >= status.actor_hourly_limit;
              return (
                <div key={actor} className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-[10px] text-charcoal-soft">
                    {actor}
                  </span>
                  <span className={`shrink-0 text-[10px] font-medium ${hit ? "text-red-600" : "text-charcoal"}`}>
                    {count}{status.actor_hourly_limit > 0 && ` / ${status.actor_hourly_limit}`}
                  </span>
                  {status.actor_hourly_limit > 0 && (
                    <div className="w-12 h-1 overflow-hidden rounded-full bg-charcoal-soft/10 shrink-0">
                      <div
                        className={`h-full rounded-full ${hit ? "bg-red-500" : actorPct >= 0.8 ? "bg-amber-500" : "bg-emerald-500"}`}
                        style={{ width: `${Math.min(100, actorPct * 100).toFixed(1)}%` }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {actorEntries.length === 0 && (
        <p className="text-[10px] text-charcoal-soft/60">No guarded actions this hour.</p>
      )}
    </div>
  );
}

function LedgerTab({
  entries, chain, mode, summary, limitsStatus, onChangeMode, onVerify, verifying,
}: {
  entries: LedgerEntry[];
  chain: ChainStatus | null;
  mode: PolicyMode | null;
  summary: LedgerSummary | null;
  limitsStatus: LimitsStatus | null;
  onChangeMode: (m: PolicyMode) => void;
  onVerify: () => void;
  verifying: boolean;
}) {
  return (
    <div className="space-y-3">
      <ModeControl mode={mode} onChange={onChangeMode} />
      {limitsStatus && <LimitsCard status={limitsStatus} />}
      {summary && <SummaryCard summary={summary} />}
      <div className="flex items-center gap-2">
        {chain ? (
          <div
            className={`flex flex-1 items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium ${
              chain.valid ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"
            }`}
          >
            <span className={`h-2 w-2 rounded-full ${chain.valid ? "bg-emerald-500" : "bg-red-500"}`} />
            {chain.valid
              ? `Verified · ${chain.checked} entries, chain intact`
              : `Tampered · break at entry #${chain.broken_at}`}
          </div>
        ) : (
          <div className="flex-1 rounded-xl bg-charcoal-soft/5 px-3 py-2 text-sm text-charcoal-soft">
            Chain not yet verified
          </div>
        )}
        <button
          onClick={onVerify}
          disabled={verifying}
          className="shrink-0 rounded-full border border-charcoal-soft/30 px-3 py-2 text-xs font-medium text-charcoal-soft hover:bg-charcoal-soft/5 disabled:opacity-50"
        >
          {verifying ? "Verifying…" : "Verify"}
        </button>
      </div>
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
                    className="rounded-full border border-rosegold/40 px-3 py-2 text-xs font-medium text-charcoal hover:bg-rosegold/10"
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

const DRY_RUN_ACTIONS = [
  { value: "make_purchase", label: "Make purchase" },
  { value: "control_device", label: "Control device" },
  { value: "send_message", label: "Send message" },
];

function PoliciesTab({
  policies, newPolicy, setNewPolicy, onAdd, onDelete, busy,
  dryRunResult, dryRunBusy, onDryRun,
}: {
  policies: Policy[];
  newPolicy: string;
  setNewPolicy: (s: string) => void;
  onAdd: () => void;
  onDelete: (id: number) => void;
  busy: boolean;
  dryRunResult: DryRunResult | null;
  dryRunBusy: boolean;
  onDryRun: (action: string, args: Record<string, unknown>) => void;
}) {
  const [drAction, setDrAction] = useState("make_purchase");
  const [drAmount, setDrAmount] = useState("75");
  const [drDevice, setDrDevice] = useState("light.living_room");
  const [drCommand, setDrCommand] = useState("on");
  const [drRecipient, setDrRecipient] = useState("boss");

  const buildArgs = (): Record<string, unknown> => {
    if (drAction === "make_purchase") return { amount: parseFloat(drAmount) || 0, item: "item" };
    if (drAction === "control_device") return { device: drDevice, command: drCommand };
    return { recipient: drRecipient, body: "hello" };
  };

  const verdictColor = dryRunResult
    ? dryRunResult.verdict === "allow" ? "text-green-700 bg-green-50 border-green-200"
    : dryRunResult.verdict === "hold"  ? "text-amber-700 bg-amber-50 border-amber-200"
    :                                    "text-red-700 bg-red-50 border-red-200"
    : "";

  return (
    <div className="space-y-4">
      {/* Add rule */}
      <div className="rounded-2xl border border-rosegold/30 bg-white/50 p-3">
        <p className="mb-2 text-xs font-medium text-charcoal-soft">Add a rule in plain language</p>
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
          className="mt-2 w-full rounded-full bg-gradient-to-br from-rosegold to-dusty py-2.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "Adding…" : "Add policy"}
        </button>
      </div>

      {/* Dry-run tester */}
      <div className="rounded-2xl border border-charcoal-soft/20 bg-white/40 p-3">
        <p className="mb-2 text-xs font-medium text-charcoal-soft">What would happen if…</p>
        <div className="flex gap-2">
          <select
            value={drAction}
            onChange={(e) => setDrAction(e.target.value)}
            className="flex-1 rounded-lg border border-charcoal-soft/20 bg-cream px-2 py-1.5 text-sm text-charcoal focus:outline-none focus:ring-1 focus:ring-rosegold"
          >
            {DRY_RUN_ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>{a.label}</option>
            ))}
          </select>
          {drAction === "make_purchase" && (
            <input
              type="number"
              value={drAmount}
              onChange={(e) => setDrAmount(e.target.value)}
              placeholder="$amount"
              className="w-24 rounded-lg border border-charcoal-soft/20 bg-cream px-2 py-1.5 text-sm text-charcoal focus:outline-none focus:ring-1 focus:ring-rosegold"
            />
          )}
          {drAction === "control_device" && (
            <>
              <input
                value={drDevice}
                onChange={(e) => setDrDevice(e.target.value)}
                placeholder="entity_id"
                className="flex-1 rounded-lg border border-charcoal-soft/20 bg-cream px-2 py-1.5 text-sm text-charcoal focus:outline-none focus:ring-1 focus:ring-rosegold"
              />
              <input
                value={drCommand}
                onChange={(e) => setDrCommand(e.target.value)}
                placeholder="command"
                className="w-20 rounded-lg border border-charcoal-soft/20 bg-cream px-2 py-1.5 text-sm text-charcoal focus:outline-none focus:ring-1 focus:ring-rosegold"
              />
            </>
          )}
          {drAction === "send_message" && (
            <input
              value={drRecipient}
              onChange={(e) => setDrRecipient(e.target.value)}
              placeholder="recipient"
              className="flex-1 rounded-lg border border-charcoal-soft/20 bg-cream px-2 py-1.5 text-sm text-charcoal focus:outline-none focus:ring-1 focus:ring-rosegold"
            />
          )}
        </div>
        <button
          onClick={() => onDryRun(drAction, buildArgs())}
          disabled={dryRunBusy}
          className="mt-2 w-full rounded-full border border-charcoal-soft/30 py-2.5 text-sm font-medium text-charcoal hover:bg-charcoal-soft/5 disabled:opacity-50"
        >
          {dryRunBusy ? "Checking…" : "Simulate"}
        </button>
        {dryRunResult && (
          <div className={`mt-2 rounded-lg border px-3 py-2 text-sm ${verdictColor}`}>
            <span className="font-semibold capitalize">{dryRunResult.verdict}</span>
            {" — "}{dryRunResult.reason}
          </div>
        )}
      </div>

      {/* Active policies */}
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
              className="rounded-full border border-red-200 px-3 py-2 text-xs font-medium text-red-600 hover:bg-red-50"
            >
              Revoke
            </button>
          ) : (
            <button
              onClick={() => onRestore(a.id)}
              className="rounded-full border border-charcoal-soft/30 px-3 py-2 text-xs font-medium text-charcoal-soft hover:bg-charcoal-soft/5"
            >
              Restore
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function KeysTab({ status, onRotated }: { status: KeysStatus | null; onRotated: () => void }) {
  const [rotating, setRotating] = useState(false);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [backup, setBackup] = useState<KeyBackup | null>(null);
  const [backupBusy, setBackupBusy] = useState(false);
  const [rotateError, setRotateError] = useState("");

  const fmt = (ts: number) =>
    new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

  const doRotate = async () => {
    setRotating(true);
    setRotateError("");
    try {
      await rotateKey();
      setConfirmRotate(false);
      onRotated();
    } catch (e) {
      setRotateError(e instanceof Error ? e.message : "Rotation failed.");
    } finally {
      setRotating(false);
    }
  };

  const doBackup = async () => {
    setBackupBusy(true);
    try {
      setBackup(await backupKey());
    } finally {
      setBackupBusy(false);
    }
  };

  if (!status) return <Empty text="Loading key info…" />;

  const pub = status.current_pub_hex;
  const pubShort = `${pub.slice(0, 16)}…${pub.slice(-8)}`;

  return (
    <div className="space-y-4">
      {/* Current key card */}
      <div className="rounded-2xl border border-charcoal-soft/15 bg-white/40 p-4">
        <p className="mb-1 text-xs font-medium text-charcoal-soft">Active signing key</p>
        <p className="font-mono text-sm text-charcoal break-all">{pubShort}</p>
        <div className="mt-2 flex items-center justify-between text-[11px] text-charcoal-soft">
          <span>
            {status.active_since ? `Active since ${fmt(status.active_since)}` : "Key age unknown"}
          </span>
          <span>
            {status.rotation_count === 0
              ? "Never rotated"
              : `${status.rotation_count} rotation${status.rotation_count > 1 ? "s" : ""}`}
          </span>
        </div>
      </div>

      {/* Rotate */}
      <div className="rounded-2xl border border-charcoal-soft/15 bg-white/40 p-3">
        <p className="mb-1 text-xs font-medium text-charcoal-soft">Rotate signing key</p>
        <p className="mb-3 text-[11px] text-charcoal-soft">
          Generates a new key. Entries signed by the retired key remain
          verifiable — the old public key is preserved in keyset.json.
          Back up your key before rotating.
        </p>
        {!confirmRotate ? (
          <button
            onClick={() => setConfirmRotate(true)}
            className="w-full rounded-full border border-amber-300 bg-amber-50 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-100"
          >
            Rotate key…
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-[11px] font-medium text-red-600">
              This will retire the current key. New entries will use a different key. Continue?
            </p>
            <div className="flex gap-2">
              <button
                onClick={doRotate}
                disabled={rotating}
                className="flex-1 rounded-full bg-gradient-to-br from-red-500 to-red-600 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                {rotating ? "Rotating…" : "Confirm rotation"}
              </button>
              <button
                onClick={() => { setConfirmRotate(false); setRotateError(""); }}
                className="flex-1 rounded-full border border-charcoal-soft/30 py-1.5 text-sm font-medium text-charcoal-soft"
              >
                Cancel
              </button>
            </div>
            {rotateError && <p className="text-[10px] text-red-600">{rotateError}</p>}
          </div>
        )}
      </div>

      {/* Backup */}
      <div className="rounded-2xl border border-charcoal-soft/15 bg-white/40 p-3">
        <p className="mb-1 text-xs font-medium text-charcoal-soft">Backup private key</p>
        <p className="mb-3 text-[11px] text-charcoal-soft">
          Export the current private key for safekeeping. Store it offline —
          never in the same place as the database. This action is logged.
        </p>
        {!backup ? (
          <button
            onClick={doBackup}
            disabled={backupBusy}
            className="w-full rounded-full border border-charcoal-soft/30 py-1.5 text-sm font-medium text-charcoal-soft hover:bg-charcoal-soft/5 disabled:opacity-50"
          >
            {backupBusy ? "Fetching…" : "Export key"}
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-[10px] font-medium text-amber-700">
              {backup.warning}
            </p>
            <textarea
              readOnly
              value={backup.private_key_hex}
              rows={3}
              onClick={(e) => (e.target as HTMLTextAreaElement).select()}
              className="w-full resize-none rounded-lg border border-charcoal-soft/20 bg-cream p-2 font-mono text-[10px] text-charcoal focus:outline-none"
            />
            <p className="text-[10px] text-charcoal-soft/60">
              Click the box to select all, then copy. Clear this tab when done.
            </p>
            <button
              onClick={() => setBackup(null)}
              className="w-full rounded-full border border-charcoal-soft/30 py-1 text-xs font-medium text-charcoal-soft hover:bg-charcoal-soft/5"
            >
              Clear
            </button>
          </div>
        )}
      </div>

      {/* Rotation history */}
      {status.history.length > 1 && (
        <div>
          <p className="mb-2 text-[10px] uppercase tracking-wide text-charcoal-soft/70">
            Key history
          </p>
          <div className="space-y-1.5">
            {status.history.map((k) => (
              <div
                key={k.pub_hex}
                className="flex items-center gap-2 rounded-xl border border-charcoal-soft/10 bg-white/30 px-3 py-2"
              >
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${k.active ? "bg-emerald-500" : "bg-charcoal-soft/30"}`} />
                <span className="flex-1 min-w-0 truncate font-mono text-[10px] text-charcoal-soft">
                  {k.pub_hex.slice(0, 16)}…
                </span>
                <span className="shrink-0 text-[10px] text-charcoal-soft/60">
                  {k.active ? "active" : k.rotated_out ? `until ${fmt(k.rotated_out)}` : "retired"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
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
