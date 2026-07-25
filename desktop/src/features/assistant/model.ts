export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  provider?: string;
  model?: string;
};

export type Proposal = {
  token: string;
  actions: import("../../shared/types").AssistantAction[];
  status: "pending" | "running" | "completed" | "failed";
  feedback: string;
};

export const ASSISTANT_STORAGE_KEY = "dyna-ai-chat-v1";
export const MAX_SAVED_MESSAGES = 40;

export function messageId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function loadMessages(): ChatMessage[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(ASSISTANT_STORAGE_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && (item.role === "user" || item.role === "assistant") && typeof item.content === "string")
      .slice(-MAX_SAVED_MESSAGES);
  } catch {
    return [];
  }
}

export function messageTime(value: string, locale: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(parsed);
}

export function assistantDisplayText(value: string): string {
  let text = String(value || "").trim();
  for (let depth = 0; depth < 3; depth += 1) {
    const fenced = text.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
    if (fenced) text = fenced[1].trim();
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      const start = text.indexOf("{");
      const end = text.lastIndexOf("}");
      if (start < 0 || end <= start) break;
      try {
        parsed = JSON.parse(text.slice(start, end + 1));
      } catch {
        break;
      }
    }
    if (typeof parsed === "string") {
      text = parsed.trim();
      continue;
    }
    if (parsed && typeof parsed === "object" && "reply" in parsed) {
      const reply = String((parsed as { reply?: unknown }).reply || "").trim();
      if (reply) return reply;
    }
    break;
  }
  return value;
}
