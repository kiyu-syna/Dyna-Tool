import { useState, type Dispatch, type SetStateAction } from "react";
import { request } from "../../../shared/api/client";
import { usePolling } from "../../../shared/hooks/usePolling";
import type { Job } from "../../../shared/types";

type Localize = (vi: string, en: string, zh: string) => string;

type PublishQueueOptions = {
  l: Localize;
  setMessage: Dispatch<SetStateAction<string>>;
  setError: Dispatch<SetStateAction<string>>;
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function usePublishQueue({ l, setMessage, setError }: PublishQueueOptions) {
  const jobs = usePolling<{ jobs: Job[]; total: number }>("/api/publisher/jobs?limit=500", 2_000);
  const [busyJob, setBusyJob] = useState("");

  async function jobAction(action: "retry" | "cancel", job: Job) {
    const key = `${job.profile_id}:${job.video_id}:${action}`;
    setBusyJob(key);
    setError("");
    try {
      await request(`/api/publisher/jobs/${action}`, {
        method: "POST",
        body: { profile_id: job.profile_id, video_id: job.video_id },
      });
      await jobs.refresh();
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusyJob("");
    }
  }

  async function resetQueue() {
    if (
      !jobs.data?.total ||
      !window.confirm(
        l(
          `Reset toàn bộ ${jobs.data.total} video trong hàng đợi Trung tâm đăng? File video gốc sẽ được giữ nguyên.`,
          `Reset all ${jobs.data.total} videos in the Publish Center queue? Original video files will be kept.`,
          `重置发布中心队列中的全部 ${jobs.data.total} 个视频？原始视频文件将保留。`,
        ),
      )
    )
      return;
    setBusyJob("queue-reset");
    setError("");
    try {
      const result = await request<{ reset_count: number }>("/api/publisher/jobs/reset", {
        method: "POST",
      });
      await jobs.refresh();
      setMessage(
        l(
          `Đã reset ${result.reset_count} video khỏi hàng đợi.`,
          `Reset ${result.reset_count} videos from the queue.`,
          `已从队列中重置 ${result.reset_count} 个视频。`,
        ),
      );
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusyJob("");
    }
  }

  return { jobs, busyJob, jobAction, resetQueue };
}
