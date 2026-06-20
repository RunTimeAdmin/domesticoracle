// REST helpers for Ora's trust layer: ledger, policies, agents, approvals.
//
// Auth: all requests include credentials: "include" so the HttpOnly session
// cookie (set by POST /auth/login) rides along automatically. No token is
// stored in JS or baked into the bundle.

const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(`GET ${path} failed (${res.status})`);
  return res.json();
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${path} failed (${res.status})`);
  return res.json();
}

async function putJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`PUT ${path} failed (${res.status})`);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`DELETE ${path} failed (${res.status})`);
  return res.json();
}

export async function login(passphrase: string): Promise<boolean> {
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ passphrase }),
  });
  return res.ok;
}

export async function logout(): Promise<void> {
  await fetch(`${API_URL}/auth/logout`, { method: "POST", credentials: "include" });
}

export async function checkSession(): Promise<boolean> {
  const res = await fetch(`${API_URL}/auth/session`, { credentials: "include" });
  return res.ok;
}

// ---- Types ----
export interface LedgerEntry {
  id: number;
  ts: number;
  actor_id: string;
  action: string;
  args_summary: string;
  decision: string;
  status: string;
  outcome: string;
  prev_hash: string;
  hash: string;
}

export interface ChainStatus {
  valid: boolean;
  checked: number;
  broken_at: number | null;
}

export interface Policy {
  id: number;
  rule_type: string;
  params: Record<string, unknown>;
  source: string;
  label: string;
}

export interface Agent {
  id: string;
  name: string;
  status: "active" | "revoked";
}

export interface PendingApproval {
  id: string;
  actor_id: string;
  action: string;
  args: Record<string, unknown>;
  summary: string;
  reason: string;
  ledger_id: number;
  created: number;
}

export type PolicyMode = "enforced" | "audit_only" | "permissive";

export interface LedgerSummary {
  window_days: number;
  total: number;
  by_category: Record<string, number>;
  held: number;
  denied: number;
  executed: number;
  financial_total: number;
  avg_risk: number;
  max_risk: number;
  trust_load: number;
}

export interface Device {
  entity_id: string;
  name: string;
  state: string;
  domain: string;
}

// ---- Calls ----
export const getLedger = () =>
  getJSON<{ entries: LedgerEntry[] }>("/ledger?limit=100").then((d) => d.entries);

export const verifyLedger = () => getJSON<ChainStatus>("/ledger/verify");

export const getPolicies = () =>
  getJSON<{ policies: Policy[] }>("/policies").then((d) => d.policies);

export const addPolicy = (text: string) =>
  postJSON<{ policy: Policy }>("/policies", { text });

export const deletePolicy = (id: number) => del<{ deleted: boolean }>(`/policies/${id}`);

export const getAgents = () =>
  getJSON<{ agents: Agent[] }>("/agents").then((d) => d.agents);

export const revokeAgent = (id: string) =>
  postJSON<{ ok: boolean }>(`/agents/${id}/revoke`);

export const restoreAgent = (id: string) =>
  postJSON<{ ok: boolean }>(`/agents/${id}/restore`);

export const getApprovals = () =>
  getJSON<{ approvals: PendingApproval[] }>("/approvals").then((d) => d.approvals);

export const resolveApproval = (id: string, decision: "approve" | "deny") =>
  postJSON<{ ok: boolean; status?: string; result?: string }>(
    `/approvals/${id}/resolve`,
    { decision }
  );

export const getMode = () => getJSON<{ mode: PolicyMode }>("/policy/mode").then((d) => d.mode);

export const setMode = (mode: PolicyMode) =>
  putJSON<{ mode: PolicyMode }>("/policy/mode", { mode }).then((d) => d.mode);

export const getSummary = () => getJSON<LedgerSummary>("/ledger/summary");

export const getDevices = () =>
  getJSON<{ configured: boolean; devices: Device[] }>("/devices");

export const controlDevice = (device: string, action: string) =>
  postJSON<{ text: string; approval: unknown | null }>("/devices/control", {
    device,
    action,
  });

export interface DryRunResult {
  action: string;
  args: Record<string, unknown>;
  verdict: "allow" | "hold" | "deny";
  reason: string;
}

export const dryrunPolicy = (action: string, args: Record<string, unknown>) =>
  postJSON<DryRunResult>("/policy/dryrun", { action, args });

export interface LimitsStatus {
  actor_hourly_limit: number;
  daily_cap: number;
  today: string;
  daily_count: number;
  daily_remaining: number | null;
  current_hour_bucket: number;
  actor_counts_this_hour: Record<string, number>;
}

export const getLimitsStatus = () => getJSON<LimitsStatus>("/limits/status");

export interface MonitorResult {
  ok: boolean | null;
  checked: number;
  broken_at: number | null;
  reason: string;
  checked_at: number;
}

export interface MonitorStatus {
  last_result: MonitorResult | null;
  verify_interval_seconds: number;
  next_check_in_seconds: number | null;
}

export const getMonitorStatus = () => getJSON<MonitorStatus>("/monitor/status");

export interface KeyEntry {
  pub_hex: string;
  rotated_in: number;
  rotated_out: number | null;
  active: boolean;
}

export interface KeysStatus {
  current_pub_hex: string;
  rotation_count: number;
  active_since: number | null;
  history: KeyEntry[];
}

export interface KeyBackup {
  private_key_hex: string;
  public_key_hex: string;
  warning: string;
}

export const getKeysStatus = () => getJSON<KeysStatus>("/keys/status");
export const rotateKey = () => postJSON<{ new_pub_hex: string; retired_pub_hex: string; rotated_at: number; rotation_count: number }>("/keys/rotate");
export const backupKey = () => getJSON<KeyBackup>("/keys/backup");
