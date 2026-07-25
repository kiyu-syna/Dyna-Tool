import { useState } from "react";
import { request } from "../../../shared/api/client";
import { useI18n } from "../../../shared/i18n";
import type { PublishBatchResult } from "../../../shared/types";
import { usePublishDraft } from "./usePublishDraft";
import { usePublishQueue } from "./usePublishQueue";
import { usePublishTargets } from "./usePublishTargets";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function usePublishCenter() {
  const { l } = useI18n();
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const draft = usePublishDraft({ l, setMessage, setError });
  const targetSelection = usePublishTargets({ setError });
  const queue = usePublishQueue({ l, setMessage, setError });
  const canSubmit =
    draft.validItems &&
    targetSelection.selectedTargets.length > 0 &&
    targetSelection.selectedTargetsReady &&
    !submitting;

  async function publish() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError("");
    setMessage("");
    try {
      const result = await request<PublishBatchResult>("/api/publisher/jobs", {
        method: "POST",
        body: {
          items: draft.items.map((item) => ({
            file_path: item.file_path,
            caption: item.caption.trim(),
            scheduled_at: item.scheduled_at ? new Date(item.scheduled_at).toISOString() : "",
          })),
          targets: targetSelection.selectedTargets,
        },
        timeoutMs: 120_000,
      });
      draft.setItems([]);
      const scheduleText = result.scheduled_count
        ? l(
            ` ${result.scheduled_count} video đã được đặt lịch.`,
            ` ${result.scheduled_count} videos were scheduled.`,
            ` 已为 ${result.scheduled_count} 个视频排期。`,
          )
        : "";
      setMessage(
        l(
          `Đã tạo ${result.job_count} tác vụ từ ${result.file_count} video.${scheduleText}`,
          `Created ${result.job_count} jobs from ${result.file_count} videos.${scheduleText}`,
          `已从 ${result.file_count} 个视频创建 ${result.job_count} 个任务。${scheduleText}`,
        ),
      );
      await queue.jobs.refresh();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return {
    l,
    ...draft,
    ...targetSelection,
    ...queue,
    submitting,
    message,
    error,
    canSubmit,
    publish,
  };
}
