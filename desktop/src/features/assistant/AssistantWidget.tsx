import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  LoaderCircle,
  MessageCircle,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRound,
} from "lucide-react";
import { useAssistantController } from "./hooks/useAssistantController";
import { assistantDisplayText, messageTime } from "./model";
import "./assistant.css";

export default function AssistantWidget() {
  const {
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
  } = useAssistantController();

  return (
    <div className={`assistant-widget ${open ? "open" : ""}`}>
      {open && (
        <section
          id="dyna-ai-chat-panel"
          className="assistant-chat assistant-floating-panel"
          role="dialog"
          aria-label={l("Trợ lý AI Dyna", "Dyna AI Assistant", "Dyna AI 助手")}
        >
          <header className="assistant-chat-header">
            <div className="assistant-identity">
              <span>
                <Bot size={20} />
                <i aria-hidden="true" />
              </span>
              <div>
                <strong>Dyna AI</strong>
                <small>
                  <i aria-hidden="true" />
                  {l(
                    "Sẵn sàng hỗ trợ · Thao tác có xác nhận",
                    "Ready to help · Confirmed actions",
                    "随时协助 · 操作需确认",
                  )}
                </small>
              </div>
            </div>
            <div className="assistant-header-actions">
              <button
                className="icon-button"
                onClick={clearChat}
                disabled={!messages.length && !proposal}
                title={l("Xóa trò chuyện", "Clear chat", "清空对话")}
                aria-label={l("Xóa trò chuyện", "Clear chat", "清空对话")}
              >
                <Trash2 size={15} />
              </button>
              <button
                className="icon-button"
                onClick={() => setOpen(false)}
                title={l("Thu nhỏ", "Minimize", "最小化")}
                aria-label={l("Thu nhỏ cửa sổ chat", "Minimize chat", "最小化聊天窗口")}
              >
                <ChevronDown size={17} />
              </button>
            </div>
          </header>

          <div className="assistant-stream" ref={streamRef} aria-live="polite">
            {!messages.length && (
              <div className="assistant-welcome">
                <span>
                  <Sparkles size={22} />
                </span>
                <h2>{l("Xin chào, mình là Dyna AI", "Hi, I'm Dyna AI", "您好，我是 Dyna AI")}</h2>
                <p>
                  {l(
                    "Hỏi mình về hồ sơ, lỗi đăng, hàng đợi hoặc yêu cầu một thao tác trong Dyna.",
                    "Ask about Profiles, publishing errors, queues, or request an action in Dyna.",
                    "您可以询问配置文件、发布错误、队列，或请求在 Dyna 中执行操作。",
                  )}
                </p>
                <div className="assistant-suggestions">
                  {suggestions.map((suggestion) => (
                    <button key={suggestion} onClick={() => void sendMessage(suggestion)}>
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((message) => (
              <article key={message.id} className={`assistant-message ${message.role}`}>
                <span className="assistant-avatar">
                  {message.role === "assistant" ? <Bot size={17} /> : <UserRound size={17} />}
                </span>
                <div>
                  <header>
                    <strong>{message.role === "assistant" ? "Dyna AI" : l("Bạn", "You", "您")}</strong>
                    <small>{messageTime(message.createdAt, locale)}</small>
                  </header>
                  <p>{message.role === "assistant" ? assistantDisplayText(message.content) : message.content}</p>
                </div>
              </article>
            ))}

            {sending && (
              <article className="assistant-message assistant loading">
                <span className="assistant-avatar">
                  <Bot size={17} />
                </span>
                <div>
                  <header>
                    <strong>Dyna AI</strong>
                  </header>
                  <p>
                    <LoaderCircle className="spin" size={16} />
                    {l("Đang phân tích Dyna...", "Analyzing Dyna...", "正在分析 Dyna...")}
                  </p>
                </div>
              </article>
            )}

            {proposal && (
              <article className={`assistant-proposal ${proposal.status}`}>
                <header>
                  <ShieldCheck size={18} />
                  <div>
                    <strong>{l("Đề xuất hành động", "Proposed actions", "建议操作")}</strong>
                    <small>
                      {l("Chỉ thực hiện sau khi bạn xác nhận", "Runs only after your confirmation", "仅在您确认后执行")}
                    </small>
                  </div>
                </header>
                <ul>
                  {proposal.actions.map((action, index) => (
                    <li key={`${action.type}-${index}`}>
                      <CheckCircle2 size={14} />
                      {actionLabel(action)}
                    </li>
                  ))}
                </ul>
                {proposal.feedback && (
                  <p className="assistant-proposal-feedback">
                    <AlertTriangle size={14} />
                    {proposal.feedback}
                  </p>
                )}
                {proposal.status === "pending" && (
                  <button className="primary-button" onClick={() => void confirmProposal()}>
                    <ShieldCheck size={15} />
                    {l("Xác nhận thực hiện", "Confirm and run", "确认执行")}
                  </button>
                )}
                {proposal.status === "running" && (
                  <button className="primary-button" disabled>
                    <LoaderCircle className="spin" size={15} />
                    {l("Đang thực hiện...", "Running...", "正在执行...")}
                  </button>
                )}
              </article>
            )}

            {error && (
              <div className="assistant-error">
                <AlertTriangle size={16} />
                <span>{error}</span>
              </div>
            )}
          </div>

          <footer className="assistant-composer">
            <textarea
              ref={composerRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
              placeholder={l("Nhắn cho Dyna AI...", "Message Dyna AI...", "给 Dyna AI 发送消息...")}
              rows={1}
              maxLength={12000}
              disabled={sending}
            />
            <button
              className="primary-button assistant-send"
              onClick={() => void sendMessage()}
              disabled={sending || !draft.trim()}
              title={l("Gửi", "Send", "发送")}
            >
              <Send size={17} />
            </button>
            <small>
              <ShieldCheck size={12} />
              {l(
                "API key được bảo vệ trên máy chủ Dyna",
                "API keys are protected on the Dyna server",
                "API 密钥受 Dyna 服务器保护",
              )}
            </small>
          </footer>
        </section>
      )}

      <button
        className={`assistant-launcher ${open ? "active" : ""}`}
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="dyna-ai-chat-panel"
        aria-label={
          open
            ? l("Đóng Trợ lý AI", "Close AI Assistant", "关闭 AI 助手")
            : l("Mở Trợ lý AI", "Open AI Assistant", "打开 AI 助手")
        }
        title={
          open
            ? l("Thu nhỏ Trợ lý AI", "Minimize AI Assistant", "最小化 AI 助手")
            : l("Mở Dyna AI", "Open Dyna AI", "打开 Dyna AI")
        }
      >
        {open ? (
          <ChevronDown size={22} />
        ) : (
          <>
            <span className="assistant-launcher-icon">
              <MessageCircle size={20} />
              <i aria-hidden="true" />
            </span>
            <span className="assistant-launcher-copy">
              <strong>Dyna AI</strong>
              <small>{l("Hỏi hoặc điều khiển Dyna", "Ask or control Dyna", "询问或控制 Dyna")}</small>
            </span>
            {unread > 0 && <b className="assistant-unread">{unread > 9 ? "9+" : unread}</b>}
          </>
        )}
      </button>
    </div>
  );
}
