"use client";

import { useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  getLedger, verifyLedger, getPolicies, addPolicy, deletePolicy,
  getAgents, revokeAgent, restoreAgent, getApprovals, resolveApproval,
  getMode, setMode, getSummary, getDevices, controlDevice, dryrunPolicy,
  getLimitsStatus, getKeysStatus, rotateKey, backupKey, getMonitorStatus,
  getMcpInfo, subscribeDeviceEvents,
  LedgerEntry, ChainStatus, Policy, Agent, PendingApproval,
  PolicyMode, LedgerSummary, Device, DeviceAttributes, DryRunResult, LimitsStatus,
  KeysStatus, KeyBackup, MonitorStatus, ProvenanceRecord, McpInfo,
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
  const [monitorStatus, setMonitorStatus] = useState<MonitorStatus | null>(null);
  const [mcpInfo, setMcpInfo] = useState<McpInfo | null>(null);
  const [verifying, setVerifying] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [p, l, pol, ag, m, s, dev, lim, ks, mon, mcp] = await Promise.all([
        getApprovals(), getLedger(), getPolicies(), getAgents(),
        getMode(), getSummary(), getDevices(), getLimitsStatus(), getKeysStatus(),
        getMonitorStatus(), getMcpInfo(),
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
      setMonitorStatus(mon);
      setMcpInfo(mcp);
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

  const control = async (
    device: string,
    action: string,
    extra?: { brightness?: number; temperature?: number }
  ) => {
    await controlDevice(device, action, extra);
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
                  monitorStatus={monitorStatus}
                  onChangeMode={changeMode}
                  onVerify={verifyChain}
                  verifying={verifying}
                />
              )}
              {tab === "devices" && (
                <DevicesTab
                  devices={devices}
                  live={haLive}
                  open={open}
                  active={tab === "devices"}
                  onControl={control}
                />
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
                  mcpInfo={mcpInfo}
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

function MonitorCard({ status }: { status: MonitorStatus }) {
  const last = status.last_result;
  const interval = Math.round(status.verify_interval_seconds / 60);
  const nextIn = status.next_check_in_seconds;

  const relTime = (ts: number) => {
    const mins = Math.round((Date.now() / 1000 - ts) / 60);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m ago`;
  };

  const nextLabel = nextIn == null
    ? "pending first check"
    : nextIn < 60
    ? "< 1 min"
    : `${Math.round(nextIn / 60)}m`;

  return (
    <div className="rounded-2xl border border-charcoal-soft/15 bg-white/40 p-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-charcoal-soft">Integrity monitor</p>
        <span className="text-[10px] text-charcoal-soft/60">every {interval}m · next {nextLabel}</span>
      </div>

      {!last ? (
        <p className="mt-1.5 text-[11px] text-charcoal-soft/70">
          First check runs 30 s after startup.
        </p>
      ) : last.ok ? (
        <div className="mt-1.5 flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0" />
          <span className="text-[11px] text-charcoal-soft">
            {last.checked} entries verified · {relTime(last.checked_at)}
          </span>
        </div>
      ) : (
        <div className="mt-1.5 space-y-1">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-red-500 shrink-0" />
            <span className="text-[11px] font-medium text-red-700">Chain integrity failure</span>
          </div>
          <p className="text-[10px] text-red-600 pl-4">{last.reason}</p>
          {last.broken_at && (
            <p className="text-[10px] text-red-600 pl-4">Broken at entry #{last.broken_at}</p>
          )}
        </div>
      )}
    </div>
  );
}

function parseProvenance(args_json?: string): ProvenanceRecord | null {
  if (!args_json) return null;
  try {
    const j = JSON.parse(args_json);
    return j._provenance ?? null;
  } catch {
    return null;
  }
}

function ProvenanceBadge({ prov }: { prov: ProvenanceRecord }) {
  const [open, setOpen] = useState(false);
  const hasSigs = prov.signals.length > 0;

  return (
    <div className="relative">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        title={hasSigs ? `${prov.signals.length} injection signal(s)` : "Content scanned — clean"}
        className={`flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium transition ${
          hasSigs
            ? "bg-amber-50 text-amber-700 border border-amber-200"
            : "bg-emerald-50 text-emerald-700 border border-emerald-200"
        }`}
      >
        {/* Shield icon */}
        <svg width="10" height="11" viewBox="0 0 12 14" fill="none" className="shrink-0">
          <path d="M6 1L1 3.5V7C1 10 3.5 12.5 6 13C8.5 12.5 11 10 11 7V3.5L6 1Z"
            fill={hasSigs ? "#d97706" : "#059669"} fillOpacity="0.15"
            stroke={hasSigs ? "#d97706" : "#059669"} strokeWidth="1.2" />
          {hasSigs && (
            <text x="6" y="9.5" textAnchor="middle" fontSize="7" fill="#d97706" fontWeight="bold">!</text>
          )}
        </svg>
        {hasSigs ? prov.signals.length : "✓"}
      </button>

      {open && (
        <div
          className="absolute right-0 top-6 z-10 w-64 rounded-xl border border-charcoal-soft/20 bg-white shadow-lg p-3 text-xs"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="mb-1.5 font-medium text-charcoal">Provenance</p>

          {prov.sources.length > 0 && (
            <div className="mb-2">
              <p className="text-[10px] font-medium text-charcoal-soft/70 uppercase tracking-wide mb-1">Sources</p>
              <div className="space-y-0.5">
                {prov.sources.map((s, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <span className="rounded bg-charcoal-soft/10 px-1 py-0.5 text-[10px] font-mono text-charcoal-soft">{s.type}</span>
                    <span className="truncate text-[10px] text-charcoal-soft">{s.id}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {hasSigs ? (
            <div>
              <p className="text-[10px] font-medium text-amber-700/80 uppercase tracking-wide mb-1">Injection signals</p>
              <div className="space-y-1.5">
                {prov.signals.map((sig, i) => (
                  <div key={i} className="rounded-lg border border-amber-200 bg-amber-50 p-1.5">
                    <p className="font-medium text-amber-800">{sig.description}</p>
                    <p className="mt-0.5 font-mono text-[10px] text-amber-700 break-all">{sig.excerpt}</p>
                    {sig.source_id && (
                      <p className="mt-0.5 text-[9px] text-amber-600/70 truncate">from: {sig.source_id}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-[10px] text-emerald-700">No injection signals detected.</p>
          )}

          <p className="mt-2 text-[9px] text-charcoal-soft/50">
            Scanner v{prov.scanner_version} · {new Date(prov.scanned_at * 1000).toLocaleTimeString()}
          </p>
          <button
            onClick={() => setOpen(false)}
            className="mt-1.5 text-[10px] text-charcoal-soft hover:text-charcoal"
          >
            Close
          </button>
        </div>
      )}
    </div>
  );
}

function LedgerEntryCard({ entry: e }: { entry: LedgerEntry }) {
  const prov = parseProvenance(e.args_json);
  return (
    <div className="rounded-xl border border-charcoal-soft/15 bg-white/40 p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-charcoal truncate">{e.action}</span>
        <div className="flex items-center gap-1.5 shrink-0">
          {prov && <ProvenanceBadge prov={prov} />}
          <StatusPill status={e.status} />
        </div>
      </div>
      <p className="mt-0.5 text-xs text-charcoal-soft">{e.args_summary}</p>
      {e.outcome && <p className="mt-1 text-xs text-charcoal-soft">{e.outcome}</p>}
      <div className="mt-1.5 flex items-center justify-between text-[10px] text-charcoal-soft/70">
        <span>{e.actor_id}</span>
        <span title={e.hash}>#{e.id} · {e.hash.slice(0, 10)}…</span>
      </div>
    </div>
  );
}

function LedgerTab({
  entries, chain, mode, summary, limitsStatus, monitorStatus, onChangeMode, onVerify, verifying,
}: {
  entries: LedgerEntry[];
  chain: ChainStatus | null;
  mode: PolicyMode | null;
  summary: LedgerSummary | null;
  limitsStatus: LimitsStatus | null;
  monitorStatus: MonitorStatus | null;
  onChangeMode: (m: PolicyMode) => void;
  onVerify: () => void;
  verifying: boolean;
}) {
  return (
    <div className="space-y-3">
      <ModeControl mode={mode} onChange={onChangeMode} />
      {limitsStatus && <LimitsCard status={limitsStatus} />}
      {summary && <SummaryCard summary={summary} />}
      {monitorStatus && <MonitorCard status={monitorStatus} />}
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
        entries.map((e) => <LedgerEntryCard key={e.id} entry={e} />)
      )}
    </div>
  );
}

// HA state color map — exact values from home-assistant/frontend color.globals.ts
function haStateColor(domain: string, state: string): { color: string; bg: string } {
  const C = {
    amber:  { color: "#f59e0b", bg: "rgba(245,158,11,0.13)"  },
    cyan:   { color: "#00bcd4", bg: "rgba(0,188,212,0.13)"   },
    green:  { color: "#4caf50", bg: "rgba(76,175,80,0.13)"   },
    red:    { color: "#f44336", bg: "rgba(244,67,54,0.13)"   },
    orange: { color: "#ff6f22", bg: "rgba(255,111,34,0.13)"  },
    blue:   { color: "#2196f3", bg: "rgba(33,150,243,0.13)"  },
    lblue:  { color: "#03a9f4", bg: "rgba(3,169,244,0.13)"   },
    grey:   { color: "#9e9e9e", bg: "rgba(158,158,158,0.11)" },
  };
  if (domain === "light" || domain === "switch" || domain === "input_boolean")
    return state === "on" ? C.amber : C.grey;
  if (domain === "fan") return state === "on" ? C.cyan : C.grey;
  if (domain === "lock") {
    if (state === "locked") return C.green;
    if (state === "unlocked") return C.red;
    return C.grey;
  }
  if (domain === "cover") return state === "open" ? { color: "#926bc7", bg: "rgba(146,107,199,0.13)" } : C.grey;
  if (domain === "climate") {
    if (state === "heat") return C.orange;
    if (state === "cool") return C.blue;
    if (state === "auto" || state === "heat_cool") return C.green;
    if (state === "fan_only") return C.cyan;
    return C.grey;
  }
  if (domain === "media_player")
    return state === "playing" || state === "on" ? C.lblue : C.grey;
  if (domain === "scene" || domain === "script") return C.amber;
  return C.grey;
}

// Inline SVG paths for each device domain (24×24 viewBox, Material Design)
const DOMAIN_PATHS: Record<string, string> = {
  light:
    "M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7zm-3 18v1a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1v-1H9z",
  switch:
    "M17 7H7a5 5 0 0 0 0 10h10a5 5 0 0 0 0-10zm0 8a3 3 0 1 1 0-6 3 3 0 0 1 0 6z",
  fan:
    "M12 11a1 1 0 0 0-1 1 1 1 0 0 0 1 1 1 1 0 0 0 1-1 1 1 0 0 0-1-1zm0-9C7.03 2 4.87 6.31 7.75 9l1.31-1.31A5 5 0 0 1 12 7a5 5 0 0 1 2.94.69l1.31-1.31C19.13 3.69 16.97 2 12 2zm0 18c4.97 0 7.13-4.31 4.25-7l-1.31 1.31A5 5 0 0 1 12 17a5 5 0 0 1-2.94-.69l-1.31 1.31C4.87 20.31 7.03 22 12 22z",
  lock_locked:
    "M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z",
  lock_unlocked:
    "M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6h2c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z",
  cover:
    "M20 11H4v2h16v-2zm-8-8L4 7v2l8-3.5L20 9V7l-8-4zm0 18v-2.5l-4 1.5V22h8v-2.5l-4-1.5z",
  climate:
    "M15 13V5a3 3 0 0 0-6 0v8a5 5 0 1 0 6 0zm-3 5a3 3 0 1 1 0-6 3 3 0 0 1 0 6z",
  media_player:
    "M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z",
  input_boolean:
    "M17 7H7a5 5 0 0 0 0 10h10a5 5 0 0 0 0-10zM7 15a3 3 0 1 1 0-6 3 3 0 0 1 0 6z",
  scene:
    "M12 3L1 9l4 2.18V17h2v-4.82l2 1.09V17c0 1.1.9 2 2 2s2-.9 2-2v-3.73l4-2.18V17h2V11.18L23 9 12 3z",
  script:
    "M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0l4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z",
};

function DomainIcon({ domain, state, size = 20 }: { domain: string; state: string; size?: number }) {
  const key = domain === "lock"
    ? (state === "locked" ? "lock_locked" : "lock_unlocked")
    : domain;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d={DOMAIN_PATHS[key] ?? DOMAIN_PATHS.script} />
    </svg>
  );
}

// lock, cover, climate, media_player span both grid columns and use horizontal layout
const WIDE_DOMAINS = new Set(["lock", "cover", "climate", "media_player"]);

function brightnessPct(attrs?: DeviceAttributes): number | null {
  if (attrs?.brightness == null) return null;
  return Math.round((attrs.brightness / 255) * 100);
}

function DeviceCard({
  device: d,
  onControl,
}: {
  device: Device;
  onControl: (entityId: string, action: string, extra?: { brightness?: number; temperature?: number }) => void;
}) {
  const attrs: DeviceAttributes = d.attributes ?? {};
  const { color, bg } = haStateColor(d.domain, d.state);
  const bPct = brightnessPct(attrs);
  const [sliderVal, setSliderVal] = useState<number>(bPct ?? 100);
  const [sliding, setSliding] = useState(false);

  useEffect(() => {
    if (!sliding && bPct != null) setSliderVal(bPct);
  }, [bPct, sliding]);

  const handleBrightness = (pct: number) => {
    onControl(d.entity_id, "on", { brightness: Math.round((pct / 100) * 255) });
  };

  const handleTemp = (delta: number) => {
    const cur = attrs.temperature ?? 70;
    onControl(d.entity_id, "set_temperature", { temperature: Math.round((cur + delta) * 10) / 10 });
  };

  const toggleAction =
    d.domain === "lock"         ? (d.state === "locked"   ? "unlock" : "lock")
    : d.domain === "cover"      ? (d.state === "open"     ? "close"  : "open")
    : d.domain === "media_player" ? (d.state === "playing" ? "pause"  : "play")
    : d.state === "on"          ? "off" : "on";

  const toggleLabel =
    d.domain === "lock"         ? (d.state === "locked"   ? "Unlock" : "Lock")
    : d.domain === "cover"      ? (d.state === "open"     ? "Close"  : "Open")
    : d.domain === "media_player" ? (d.state === "playing" ? "Pause"  : "Play")
    : d.state === "on"          ? "Turn off" : "Turn on";

  const showToggle = d.domain !== "climate" && d.domain !== "scene" && d.domain !== "script";

  const stateLabel = (() => {
    if (d.domain === "climate" && attrs.hvac_mode) {
      const cur = attrs.current_temperature != null
        ? ` · ${attrs.current_temperature}${attrs.unit_of_measurement ?? "°"}` : "";
      return `${attrs.hvac_mode}${cur}`;
    }
    if (d.domain === "light" && d.state === "on" && bPct != null) return `on · ${bPct}%`;
    if (d.domain === "fan" && d.state === "on" && attrs.percentage != null) return `on · ${attrs.percentage}%`;
    return d.state;
  })();

  const iconEl = (
    <div
      className="flex shrink-0 items-center justify-center rounded-full"
      style={{ width: 36, height: 36, background: bg, color }}
    >
      <DomainIcon domain={d.domain} state={d.state} />
    </div>
  );

  const actionBtn = showToggle && (
    <button
      onClick={() => onControl(d.entity_id, toggleAction)}
      className="shrink-0 rounded-full border border-charcoal-soft/20 px-2.5 py-1 text-[11px] font-medium text-charcoal-soft hover:bg-charcoal-soft/5 transition-colors whitespace-nowrap"
    >
      {toggleLabel}
    </button>
  );

  if (WIDE_DOMAINS.has(d.domain)) {
    return (
      <div className="rounded-xl border border-charcoal-soft/10 bg-white/60 p-3 space-y-2">
        <div className="flex items-center gap-3">
          {iconEl}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-charcoal leading-tight truncate">{d.name}</p>
            <p className="text-[11px] text-charcoal-soft mt-0.5 capitalize">{stateLabel}</p>
          </div>
          {actionBtn}
        </div>

        {d.domain === "climate" && attrs.temperature != null && (
          <div className="flex items-center justify-between" style={{ paddingLeft: 48 }}>
            <div>
              <span className="text-sm font-medium" style={{ color }}>
                {attrs.temperature}{attrs.unit_of_measurement ?? "°"}
              </span>
              <span className="ml-1 text-[11px] text-charcoal-soft/70">target</span>
              {attrs.current_temperature != null && (
                <span className="ml-2 text-[11px] text-charcoal-soft/50">
                  · currently {attrs.current_temperature}{attrs.unit_of_measurement ?? "°"}
                </span>
              )}
            </div>
            <div className="flex gap-1.5">
              <button onClick={() => handleTemp(-1)} className="w-6 h-6 flex items-center justify-center rounded-full border border-charcoal-soft/20 text-sm text-charcoal-soft hover:bg-charcoal-soft/5 transition-colors">−</button>
              <button onClick={() => handleTemp(+1)} className="w-6 h-6 flex items-center justify-center rounded-full border border-charcoal-soft/20 text-sm text-charcoal-soft hover:bg-charcoal-soft/5 transition-colors">+</button>
            </div>
          </div>
        )}

        {d.domain === "media_player" && attrs.media_title && (
          <p className="text-[11px] text-charcoal-soft truncate" style={{ paddingLeft: 48 }}>
            <span style={{ color }}>▶ </span>
            {attrs.media_title}
            {attrs.media_artist && <span className="text-charcoal-soft/60"> · {attrs.media_artist}</span>}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-charcoal-soft/10 bg-white/60 p-3 flex flex-col gap-2.5 h-full">
      <div className="flex items-start justify-between gap-2">
        {iconEl}
        {actionBtn}
      </div>
      <div>
        <p className="text-[13px] font-medium text-charcoal leading-tight truncate">{d.name}</p>
        <p className="text-[11px] text-charcoal-soft mt-0.5 capitalize">{stateLabel}</p>
      </div>
      {d.domain === "light" && d.state === "on" && bPct != null && (
        <div className="flex items-center gap-2 mt-auto">
          <input
            type="range"
            min={1}
            max={100}
            step={1}
            value={sliderVal}
            onChange={(e) => { setSliderVal(+e.target.value); setSliding(true); }}
            onMouseUp={(e) => { setSliding(false); handleBrightness(+(e.target as HTMLInputElement).value); }}
            onTouchEnd={(e) => { setSliding(false); handleBrightness(+(e.target as HTMLInputElement).value); }}
            className="flex-1 h-1 cursor-pointer"
            style={{ accentColor: color }}
          />
          <span className="text-[10px] text-charcoal-soft/60 w-7 text-right shrink-0">{sliderVal}%</span>
        </div>
      )}
    </div>
  );
}

function DevicesTab({
  devices: initialDevices,
  live,
  open,
  active,
  onControl,
}: {
  devices: Device[];
  live: boolean;
  open: boolean;
  active: boolean;
  onControl: (entityId: string, action: string, extra?: { brightness?: number; temperature?: number }) => void;
}) {
  const [devices, setDevices] = useState<Device[]>(initialDevices);

  useEffect(() => {
    setDevices(initialDevices);
  }, [initialDevices]);

  useEffect(() => {
    if (!open || !active || !live) return;
    const unsub = subscribeDeviceEvents((ev) => {
      if (ev.type === "snapshot") {
        setDevices(ev.devices);
      } else if (ev.type === "state_changed") {
        setDevices((prev) =>
          prev.map((d) =>
            d.entity_id === ev.entity_id
              ? { ...d, state: ev.state, attributes: ev.attributes }
              : d
          )
        );
      }
    });
    return unsub;
  }, [open, active, live]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] text-charcoal-soft">
          {live
            ? "Connected to Home Assistant · state syncs in real time"
            : "Mock home · set ORA_HA_URL + ORA_HA_TOKEN to connect a real instance"}
        </p>
        {live && (
          <span className="flex items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 shrink-0 ml-3">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            Live
          </span>
        )}
      </div>
      {devices.length === 0 ? (
        <Empty text="No devices visible." />
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {devices.map((d) => (
            <div key={d.entity_id} className={WIDE_DOMAINS.has(d.domain) ? "col-span-2" : ""}>
              <DeviceCard device={d} onControl={onControl} />
            </div>
          ))}
        </div>
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

function McpCard({ info }: { info: McpInfo }) {
  const [copied, setCopied] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const configJson = JSON.stringify(info.claude_desktop_config, null, 2);

  const copy = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="rounded-2xl border border-charcoal-soft/15 bg-white/40 p-3">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs font-medium text-charcoal-soft">MCP server</p>
        <span className={`h-2 w-2 rounded-full ${info.enabled ? "bg-emerald-500" : "bg-charcoal-soft/30"}`} />
      </div>

      {!info.enabled ? (
        <p className="text-[11px] text-charcoal-soft/70">
          Disabled. Set <code className="font-mono">ORA_MCP_ENABLED=true</code> to enable.
        </p>
      ) : (
        <>
          {/* SSE URL */}
          <div className="mb-2 flex items-center gap-1.5">
            <code className="min-w-0 flex-1 truncate rounded bg-charcoal-soft/8 px-2 py-1 text-[10px] font-mono text-charcoal">
              {info.sse_url}
            </code>
            <button
              onClick={() => copy(info.sse_url)}
              className="shrink-0 rounded-full border border-charcoal-soft/20 px-2 py-1 text-[10px] text-charcoal-soft hover:bg-charcoal-soft/5"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>

          {/* Auth badge */}
          {info.token_required && (
            <p className="mb-2 text-[10px] text-amber-700">
              Token auth active — supply <code className="font-mono">X-Ora-Mcp-Token</code> header.
            </p>
          )}

          {/* Tools */}
          <div className="mb-2">
            <p className="mb-1 text-[10px] font-medium text-charcoal-soft/70 uppercase tracking-wide">
              Exposed tools
            </p>
            <div className="space-y-0.5">
              {info.tools.map((t) => (
                <div key={t.name} className="flex items-center gap-2">
                  <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${t.guarded ? "bg-amber-400" : "bg-emerald-400"}`} />
                  <span className="font-mono text-[10px] text-charcoal font-medium shrink-0">{t.name}</span>
                  <span className="text-[10px] text-charcoal-soft/70 truncate">{t.description}</span>
                </div>
              ))}
            </div>
            <p className="mt-1 text-[9px] text-charcoal-soft/50">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400 mr-1" />amber = consent-gated
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400 ml-2 mr-1" />green = read-only
            </p>
          </div>

          {/* Claude Desktop config */}
          <button
            onClick={() => setShowConfig((v) => !v)}
            className="text-[10px] text-charcoal-soft hover:text-charcoal"
          >
            {showConfig ? "Hide" : "Show"} Claude Desktop config ↓
          </button>
          {showConfig && (
            <div className="mt-1.5">
              <pre className="overflow-x-auto rounded-lg bg-charcoal/5 p-2 text-[10px] font-mono text-charcoal leading-relaxed">
                {configJson}
              </pre>
              <button
                onClick={() => copy(configJson)}
                className="mt-1 text-[10px] text-charcoal-soft hover:text-charcoal"
              >
                Copy JSON
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function AgentsTab({
  agents, mcpInfo, onRevoke, onRestore,
}: {
  agents: Agent[];
  mcpInfo: McpInfo | null;
  onRevoke: (id: string) => void;
  onRestore: (id: string) => void;
}) {
  return (
    <div className="space-y-3">
      {mcpInfo && <McpCard info={mcpInfo} />}
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
