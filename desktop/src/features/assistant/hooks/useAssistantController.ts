import { useEffect, useMemo, useRef, useState } from "react";
import { request } from "../../../shared/api/client";
import { useI18n } from "../../../shared/i18n";
import type { AssistantAction, AssistantChatResponse, AssistantConfirmResponse } from "../../../shared/types";
import {
  ASSISTANT_STORAGE_KEY as STORAGE_KEY,
  MAX_SAVED_MESSAGES,
  loadMessages,
  messageId,
  type ChatMessage,
  type Proposal,
} from "../model";

export function useAssistantController() {
  const { l, locale } = useI18n();
  const [open, setOpen] = useState(() => localStorage.getItem("dyna-ai-widget-open") === "true");
  const [unread, setUnread] = useState(0);
  const [messages, setMessages] = useState<ChatMessage[]>(loadMessages);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const openRef = useRef(open);

  const suggestions = useMemo(
    () => [
      l("Kiểm tra tình trạng tất cả hồ sơ", "Check all Profile statuses", "检查所有配置文件状态"),
      l("Hồ sơ nào đang có lỗi?", "Which Profiles have errors?", "哪些配置文件有错误？"),
      l("Chuyển Dyna sang trạng thái Bận", "Switch Dyna to Busy mode", "将 Dyna 切换为忙碌状态"),
      l("Kiểm tra sẵn sàng đăng TikTok", "Check TikTok publishing readiness", "检查 TikTok 发布就绪状态"),
    ],
    [l],
  );

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-MAX_SAVED_MESSAGES)));
  }, [messages]);

  useEffect(() => {
    openRef.current = open;
    localStorage.setItem("dyna-ai-widget-open", String(open));
    if (open) {
      setUnread(0);
      window.setTimeout(() => composerRef.current?.focus(), 160);
    }
  }, [open]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  useEffect(() => {
    if (!open) return;
    const stream = streamRef.current;
    if (stream) stream.scrollTo({ top: stream.scrollHeight, behavior: "smooth" });
  }, [messages, open, proposal, sending]);

  async function sendMessage(value = draft) {
    const content = value.trim();
    if (!content || sending) return;
    const userMessage: ChatMessage = {
      id: messageId(),
      role: "user",
      content,
      createdAt: new Date().toISOString(),
    };
    const nextMessages = [...messages, userMessage].slice(-MAX_SAVED_MESSAGES);
    setMessages(nextMessages);
    setDraft("");
    setError("");
    setProposal(null);
    setSending(true);
    try {
      const result = await request<AssistantChatResponse>("/api/assistant/chat", {
        method: "POST",
        body: {
          messages: nextMessages.slice(-20).map(({ role, content: text }) => ({ role, content: text })),
        },
      });
      const assistantMessage: ChatMessage = {
        id: messageId(),
        role: "assistant",
        content: result.reply,
        createdAt: new Date().toISOString(),
        provider: result.provider,
        model: result.model,
      };
      setMessages((current) => [...current, assistantMessage].slice(-MAX_SAVED_MESSAGES));
      if (!openRef.current) setUnread((current) => current + 1);
      if (result.proposal_token && result.actions.length) {
        setProposal({
          token: result.proposal_token,
          actions: result.actions,
          status: "pending",
          feedback: "",
        });
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      if (!openRef.current) setUnread((current) => current + 1);
    } finally {
      setSending(false);
    }
  }

  function clearChat() {
    setMessages([]);
    setProposal(null);
    setError("");
    localStorage.removeItem(STORAGE_KEY);
  }

  function actionLabel(action: AssistantAction): string {
    if (action.type === "start_profile") {
      return l(
        `Khởi chạy hồ sơ ${action.args.profile_id}`,
        `Start Profile ${action.args.profile_id}`,
        `启动配置文件 ${action.args.profile_id}`,
      );
    }
    if (action.type === "stop_profile") {
      return l(
        `Dừng hồ sơ ${action.args.profile_id}`,
        `Stop Profile ${action.args.profile_id}`,
        `停止配置文件 ${action.args.profile_id}`,
      );
    }
    const targets = Array.isArray(action.args.targets) ? action.args.targets.length : 0;
    return l(
      `Kiểm tra sẵn sàng cho ${targets} hồ sơ`,
      `Check readiness for ${targets} Profiles`,
      `检查 ${targets} 个配置文件的就绪状态`,
    );
  }

  async function confirmProposal() {
    if (!proposal || proposal.status !== "pending") return;
    setProposal({ ...proposal, status: "running", feedback: "" });
    try {
      const result = await request<AssistantConfirmResponse>("/api/assistant/actions/confirm", {
        method: "POST",
        body: { proposal_token: proposal.token },
      });
      const failed = result.results.filter((item) => !item.ok);
      setProposal(
        (current) =>
          current && {
            ...current,
            status: failed.length ? "failed" : "completed",
            feedback: failed.length
              ? failed.map((item) => item.error || item.type).join(" · ")
              : l("Dyna đã thực hiện xong đề xuất.", "Dyna completed the proposal.", "Dyna 已完成该建议。"),
          },
      );
    } catch (caught) {
      setProposal(
        (current) =>
          current && {
            ...current,
            status: "failed",
            feedback: caught instanceof Error ? caught.message : String(caught),
          },
      );
    }
  }

  return {
    l,
    locale,
    open,
    setOpen,
    unread,
    messages,
    draft,
    setDraft,
    sending,
    error,
    proposal,
    streamRef,
    composerRef,
    suggestions,
    sendMessage,
    clearChat,
    actionLabel,
    confirmProposal,
  };
}
