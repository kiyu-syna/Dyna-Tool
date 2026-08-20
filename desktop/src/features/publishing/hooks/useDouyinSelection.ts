import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { request } from "../../../shared/api/client";
import type { DouyinSelectionSession } from "../../../shared/types";
import type { PublishItemDraft } from "../utils";

type Localize = (vi: string, en: string, zh: string) => string;

type Options = {
  l: Localize;
  items: PublishItemDraft[];
  setItems: Dispatch<SetStateAction<PublishItemDraft[]>>;
  setBatchName: Dispatch<SetStateAction<string>>;
  setMessage: Dispatch<SetStateAction<string>>;
  setError: Dispatch<SetStateAction<string>>;
};

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function draftItems(session: DouyinSelectionSession): PublishItemDraft[] {
  return [...(session.items || [])]
    .sort((left, right) => Number(left.selected_order) - Number(right.selected_order))
    .map((item) => ({
      file_path: `douyin://${session.id}/${item.video_id}`,
      caption: String(item.description || "").trim(),
      original_description: String(item.description || "").trim(),
      scheduled_at: "",
      source_type: "douyin" as const,
      selection_id: session.id,
      video_id: item.video_id,
      source_url: item.source_url,
      thumbnail_url: item.thumbnail_url,
      author_nickname: item.author_nickname,
    }));
}

export function useDouyinSelection({ l, items, setItems, setBatchName, setMessage, setError }: Options) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const [starting, setStarting] = useState(false);
  const [session, setSession] = useState<DouyinSelectionSession | null>(null);
  const loadedSession = useRef("");
  const sessionRef = useRef<DouyinSelectionSession | null>(null);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  function receive(next: DouyinSelectionSession | null) {
    if (!next) return;
    setSession(next);
    if (next.status === "ready" && loadedSession.current !== next.id) {
      const remoteItems = draftItems(next);
      const now = new Date();
      const pad = (value: number) => String(value).padStart(2, "0");
      const sourceName = String(remoteItems[0]?.author_nickname || "Douyin").trim();
      setBatchName(
        `${sourceName} · ${pad(now.getDate())}/${pad(now.getMonth() + 1)}/${now.getFullYear()} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`,
      );
      setItems((current) => {
        const currentSession = current[0]?.selection_id;
        if (current.length && currentSession !== next.id) return current;
        loadedSession.current = next.id;
        return remoteItems;
      });
      void window.dyna?.windowControl("focus");
      setMessage(
        l(
          `Đã nhận ${remoteItems.length} video từ Douyin. Hãy kiểm tra mô tả, lịch và nơi đăng.`,
          `Received ${remoteItems.length} Douyin videos. Review captions, schedules, and publishing targets.`,
          `已收到 ${remoteItems.length} 个抖音视频，请检查文案、排期和发布目标。`,
        ),
      );
    } else if (next.status === "published") {
      setMessage(
        l(
          `Dyna đã chuẩn bị ${next.published_count || 0} video; ${next.failed_count || 0} video lỗi đã được bỏ qua và lịch đã tự dồn.`,
          `Dyna prepared ${next.published_count || 0} videos; ${next.failed_count || 0} failed videos were skipped and the schedule was compacted.`,
          `Dyna 已准备 ${next.published_count || 0} 个视频；已跳过 ${next.failed_count || 0} 个失败视频并自动压缩排期。`,
        ),
      );
    } else if (next.last_error) {
      setError(next.last_error);
    }
  }

  useEffect(() => {
    let cancelled = false;
    void request<{ session: DouyinSelectionSession | null }>("/api/publisher/douyin-selections/active")
      .then((result) => {
        if (!cancelled && result.session) receive(result.session);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const current = sessionRef.current;
      const path = current?.id
        ? `/api/publisher/douyin-selections/${encodeURIComponent(current.id)}`
        : "/api/publisher/douyin-selections/active";
      void request<{ session: DouyinSelectionSession | null }>(path)
        .then((result) => receive(result.session))
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);

  function openDialog() {
    setError("");
    if (items.length) {
      setError(
        l(
          "Hãy hoàn tất hoặc xóa danh sách video hiện tại trước khi bắt đầu một phiên Douyin mới.",
          "Finish or clear the current video list before starting a new Douyin selection.",
          "开始新的抖音选择前，请先完成或清空当前视频列表。",
        ),
      );
      return;
    }
    setDialogOpen(true);
  }

  async function start() {
    if (starting) return;
    setStarting(true);
    setError("");
    setMessage("");
    try {
      const result = await request<{ session: DouyinSelectionSession }>("/api/publisher/douyin-selections", {
        method: "POST",
        body: { source_url: sourceUrl.trim() },
      });
      loadedSession.current = "";
      receive(result.session);
      setDialogOpen(false);
      await window.dyna?.openExternal(result.session.source_url);
      setMessage(
        l(
          "Đã mở trang cá nhân Douyin. Extension sẽ tự bật chế độ chọn video.",
          "Opened the Douyin profile. The extension will enable selection mode automatically.",
          "已打开抖音个人主页，扩展将自动启用视频选择模式。",
        ),
      );
    } catch (error) {
      setError(errorText(error));
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (!session?.id) return;
    try {
      await request(`/api/publisher/douyin-selections/${encodeURIComponent(session.id)}`, {
        method: "DELETE",
      });
      setSession(null);
      setItems((current) => current.filter((item) => item.selection_id !== session.id));
      setMessage(l("Đã hủy phiên chọn Douyin.", "Cancelled the Douyin selection.", "已取消抖音选择。"));
    } catch (error) {
      setError(errorText(error));
    }
  }

  return {
    dialogOpen,
    setDialogOpen,
    sourceUrl,
    setSourceUrl,
    starting,
    session,
    openDialog,
    start,
    cancel,
  };
}
