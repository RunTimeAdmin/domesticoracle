// SSE client for Domestic Oracle's /chat endpoint.
//
// The backend streams `data: {"text": "..."}` events as the reply is generated,
// then a final `data: {"done": true}`. We POST the message and parse the stream
// manually (EventSource only supports GET), invoking callbacks as text arrives.

const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

export interface ApprovalEvent {
  id: string;
  action: string;
  summary: string;
  reason: string;
  ledger_id: number;
}

export interface StreamCallbacks {
  onText: (delta: string) => void;
  onApproval?: (approval: ApprovalEvent) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export async function streamChat(
  userId: string,
  message: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  try {
    const res = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, message }),
      signal,
    });

    if (!res.ok || !res.body) {
      callbacks.onError(`Domestic Oracle couldn't be reached (status ${res.status}).`);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";

      for (const evt of events) {
        const line = evt.trim();
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;

        try {
          const data = JSON.parse(payload);
          if (data.done) {
            callbacks.onDone();
            return;
          }
          if (data.approval && callbacks.onApproval) {
            callbacks.onApproval(data.approval as ApprovalEvent);
          }
          if (typeof data.text === "string") {
            callbacks.onText(data.text);
          }
        } catch {
          // Ignore malformed fragments; the next event will likely parse.
        }
      }
    }
    callbacks.onDone();
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    callbacks.onError("Connection to Domestic Oracle was interrupted.");
  }
}

export function getUserId(): string {
  if (typeof window === "undefined") return "default";
  let id = localStorage.getItem("oracle_user_id");
  if (!id) {
    id = `user_${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem("oracle_user_id", id);
  }
  return id;
}
