import { useState } from "react";
import { request } from "../../../shared/api/client";
import { useI18n } from "../../../shared/i18n";
import type { PublishBatchResult } from "../../../shared/types";
import { usePublishDraft } from "./usePublishDraft";
import { useDouyinSelection } from "./useDouyinSelection";
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
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiCaptionProgress, setAiCaptionProgress] = useState({ completed: 0, total: 0, failed: 0 });
  const draft = usePublishDraft({ l, setMessage, setError });
  const douyinSelection = useDouyinSelection({
    l,
    items: draft.items,
    setItems: draft.setItems,
    setBatchName: draft.setBatchName,
    setMessage,
    setError,
  });
  const targetSelection = usePublishTargets({ setError });
  const queue = usePublishQueue({ l, setMessage, setError });
  const canSubmit =
    draft.validItems &&
    targetSelection.selectedTargets.length > 0 &&
    targetSelection.selectedTargetsReady &&
    Boolean(draft.batchName.trim()) &&
    !aiGenerating &&
    !submitting;

  async function generateAiCaptions(instruction: string) {
    if (aiGenerating || !draft.items.length) return;
    const normalizedInstruction = instruction.trim();
    if (!normalizedInstruction) {
      setError(
        l(
          "Hãy nhập yêu cầu để DynaAI biết cách viết mô tả.",
          "Enter instructions for DynaAI to write the captions.",
          "请输入要求，让 DynaAI 知道如何编写文案。",
        ),
      );
      return;
    }

    const snapshot = [...draft.items];
    const generated = new Map<string, string>();
    let failed = 0;
    let completed = 0;
    let nextIndex = 0;
    let modelLabel = "";
    setAiGenerating(true);
    setAiCaptionProgress({ completed: 0, total: snapshot.length, failed: 0 });
    setError("");
    setMessage("");
    try {
      const worker = async () => {
        while (nextIndex < snapshot.length) {
          const index = nextIndex;
          nextIndex += 1;
          const item = snapshot[index];
          try {
            const result = await request<{ caption: string; provider?: string; model?: string }>(
              "/api/assistant/captions/generate",
              {
                method: "POST",
                body: {
                  original_description: item.original_description || item.caption || "",
                  instruction: normalizedInstruction,
                  video_label: item.video_id || item.file_path.split(/[\\/]/).pop() || "",
                },
                timeoutMs: 90_000,
              },
            );
            if (result.caption.trim()) generated.set(item.file_path, result.caption.trim());
            else failed += 1;
            modelLabel = result.model || result.provider || modelLabel;
          } catch {
            failed += 1;
          } finally {
            completed += 1;
            setAiCaptionProgress({ completed, total: snapshot.length, failed });
          }
        }
      };
      await Promise.all(Array.from({ length: Math.min(3, snapshot.length) }, worker));
      draft.setItems((current) =>
        current.map((item) => ({
          ...item,
          caption: generated.get(item.file_path) || item.caption,
        })),
      );
      if (!generated.size) {
        setError(
          l(
            "DynaAI chưa tạo được mô tả nào. Hãy kiểm tra đăng nhập hoặc thử lại với prompt khác.",
            "DynaAI could not create any captions. Check your sign-in or try another prompt.",
            "DynaAI 未能生成任何文案，请检查登录状态或尝试其他提示词。",
          ),
        );
      } else {
        setMessage(
          l(
            `DynaAI đã tạo ${generated.size}/${snapshot.length} mô tả riêng${modelLabel ? ` bằng ${modelLabel}` : ""}. Mô tả cũ được giữ lại cho video lỗi.`,
            `DynaAI created ${generated.size}/${snapshot.length} unique captions${modelLabel ? ` with ${modelLabel}` : ""}. Existing captions were kept for failed videos.`,
            `DynaAI 已生成 ${generated.size}/${snapshot.length} 条独立文案${modelLabel ? `（${modelLabel}）` : ""}，失败视频保留原文案。`,
          ),
        );
      }
    } finally {
      setAiGenerating(false);
    }
  }

  async function publish() {
    if (!canSubmit) return;
    setSubmitting(true);
    setError("");
    setMessage("");
    try {
      const douyinItems = draft.items.filter((item) => item.source_type === "douyin");
      if (douyinItems.length) {
        if (douyinItems.length !== draft.items.length) {
          throw new Error(
            l(
              "Không thể trộn video trên máy và video Douyin trong cùng một lần tạo lịch.",
              "Local and Douyin videos cannot be mixed in one publishing batch.",
              "本机视频和抖音视频不能混合在同一发布批次中。",
            ),
          );
        }
        const selectionId = String(douyinItems[0].selection_id || "");
        await request(`/api/publisher/douyin-selections/${encodeURIComponent(selectionId)}/publish`, {
          method: "POST",
          body: {
            batch_name: draft.batchName.trim(),
            items: douyinItems.map((item) => ({
              video_id: item.video_id,
              caption: item.caption.trim(),
              scheduled_at: item.scheduled_at ? new Date(item.scheduled_at).toISOString() : "",
            })),
            targets: targetSelection.selectedTargets,
          },
          timeoutMs: 120_000,
        });
        draft.setItems([]);
        draft.renewBatchName();
        setMessage(
          l(
            `Dyna đang tải và kiểm tra ${douyinItems.length} video. Video lỗi sẽ được bỏ qua và lịch sẽ tự dồn.`,
            `Dyna is downloading and validating ${douyinItems.length} videos. Failed videos will be skipped and the schedule compacted.`,
            `Dyna 正在下载并检查 ${douyinItems.length} 个视频。失败视频将被跳过，排期会自动压缩。`,
          ),
        );
        return;
      }
      const result = await request<PublishBatchResult>("/api/publisher/jobs", {
        method: "POST",
        body: {
          batch_name: draft.batchName.trim(),
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
      draft.renewBatchName();
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
    ...douyinSelection,
    submitting,
    aiGenerating,
    aiCaptionProgress,
    message,
    error,
    canSubmit,
    generateAiCaptions,
    publish,
  };
}
