import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "../../shared/api/client";
import type {
  VideoAiCapabilities,
  VideoAiDubbing,
  VideoAiEditorSaveRequest,
  VideoAiProject,
  VideoAiRenderRequest,
  VideoAiSubtitle,
  VideoAiTtsStatus,
} from "../../shared/types";

const BUSY_STATUSES = new Set(["detecting", "transcribing", "translating", "dubbing", "rendering"]);

export function useVideoAi() {
  const [capabilities, setCapabilities] = useState<VideoAiCapabilities | null>(null);
  const [project, setProject] = useState<VideoAiProject | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [dubbingPreviewUrl, setDubbingPreviewUrl] = useState("");
  const [ttsPreviewUrl, setTtsPreviewUrl] = useState("");
  const [ttsPreviewVoice, setTtsPreviewVoice] = useState("");
  const [ttsStatus, setTtsStatus] = useState<VideoAiTtsStatus | null>(null);
  const [action, setAction] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const editorSaveQueueRef = useRef<Promise<VideoAiProject | null>>(Promise.resolve(null));

  useEffect(() => {
    void request<VideoAiCapabilities>("/api/video-ai/capabilities")
      .then(setCapabilities)
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)));
    void request<VideoAiTtsStatus>("/api/video-ai/tts/status?provider=vieneu")
      .then(setTtsStatus)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (ttsStatus?.state !== "loading") return;
    let disposed = false;
    const timer = window.setInterval(() => {
      void request<VideoAiTtsStatus>("/api/video-ai/tts/status?provider=vieneu")
        .then((status) => {
          if (!disposed) setTtsStatus(status);
        })
        .catch(() => undefined);
    }, 800);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [ttsStatus?.state]);

  useEffect(() => {
    if (!project?.dubbing_options?.generated_at) {
      setDubbingPreviewUrl("");
      return;
    }
    let disposed = false;
    void window.dyna?.localDubbingUrl(project.id).then((url) => {
      if (!disposed) {
        const separator = url?.includes("?") ? "&" : "?";
        setDubbingPreviewUrl(
          url ? `${url}${separator}v=${encodeURIComponent(project.dubbing_options?.generated_at || "")}` : "",
        );
      }
    });
    return () => {
      disposed = true;
    };
  }, [project?.dubbing_options?.generated_at, project?.id]);

  useEffect(() => {
    if (
      !project?.dubbing_options?.generated_at
      || project.dubbing_options.provider !== "vieneu"
    ) return;
    void request<VideoAiTtsStatus>("/api/video-ai/tts/status?provider=vieneu")
      .then(setTtsStatus)
      .catch(() => undefined);
  }, [project?.dubbing_options?.generated_at, project?.dubbing_options?.provider]);

  useEffect(() => {
    let disposed = false;
    void request<{ projects: VideoAiProject[] }>("/api/video-ai/projects?limit=1")
      .then(async ({ projects }) => {
        const recent = projects[0];
        if (!recent || disposed) return;
        const mediaUrl = (await window.dyna?.localMediaUrl(recent.id)) || "";
        if (disposed) return;
        setProject(recent);
        setPreviewUrl(mediaUrl);
        if (recent.status === "failed") setError(recent.error || recent.stage);
      })
      .catch((caught) => {
        if (!disposed) setError(caught instanceof Error ? caught.message : String(caught));
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    if (!project || !BUSY_STATUSES.has(project.status)) return;
    let disposed = false;
    const timer = window.setInterval(() => {
      void request<{ project: VideoAiProject }>(`/api/video-ai/projects/${project.id}`)
        .then(({ project: incoming }) => {
          if (disposed) return;
          setProject(incoming);
          if (!BUSY_STATUSES.has(incoming.status)) {
            setAction("");
            if (incoming.status === "failed") setError(incoming.error || incoming.stage);
            else if (incoming.status === "completed") setMessage(incoming.stage);
            else if (incoming.status === "ready") setMessage(incoming.stage);
          }
        })
        .catch((caught) => {
          if (!disposed) setError(caught instanceof Error ? caught.message : String(caught));
        });
    }, 900);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [project?.id, project?.status]);

  async function run<T>(name: string, operation: () => Promise<T>): Promise<T | null> {
    setAction(name);
    setError("");
    setMessage("");
    try {
      return await operation();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      return null;
    } finally {
      setAction("");
    }
  }

  async function chooseVideo() {
    const filePaths = await window.dyna?.selectMedia();
    if (!filePaths?.length) return;
    await run("select", async () => {
      const result = await request<{ project: VideoAiProject }>("/api/video-ai/projects", {
        method: "POST",
        body: { source_path: filePaths[0] },
        timeoutMs: 30_000,
      });
      setProject(result.project);
      setPreviewUrl((await window.dyna?.localMediaUrl(result.project.id)) || "");
      setMessage("Video đã sẵn sàng để xử lý.");
      return result;
    });
  }

  async function transcribe(sourceLanguage: string, modelName: string) {
    if (!project) return;
    await run("transcribe", async () => {
      const result = await request<{ project: VideoAiProject }>(
        `/api/video-ai/projects/${project.id}/transcribe`,
        {
          method: "POST",
          body: { source_language: sourceLanguage, model_name: modelName },
          timeoutMs: 30_000,
        },
      );
      setProject(result.project);
      return result;
    });
  }

  async function detectSubtitleRegion(sampleCount = 24) {
    if (!project) return;
    await run("detect", async () => {
      const result = await request<{ project: VideoAiProject }>(
        `/api/video-ai/projects/${project.id}/detect-subtitle-region`,
        {
          method: "POST",
          body: { sample_count: sampleCount },
          timeoutMs: 30_000,
        },
      );
      setProject(result.project);
      return result;
    });
  }

  async function generateDubbing(options: VideoAiDubbing) {
    if (!project) return;
    await run("dubbing", async () => {
      const result = await request<{ project: VideoAiProject }>(
        `/api/video-ai/projects/${project.id}/dubbing`,
        {
          method: "POST",
          body: options,
          timeoutMs: 30_000,
        },
      );
      setProject(result.project);
      return result;
    });
  }

  async function prepareTts(provider: VideoAiDubbing["provider"] = "vieneu") {
    await run("tts-prepare", async () => {
      const status = await request<VideoAiTtsStatus>("/api/video-ai/tts/prepare", {
        method: "POST",
        body: { provider },
        timeoutMs: 30_000,
      });
      if (provider === "vieneu") setTtsStatus(status);
      return status;
    });
  }

  async function ensureTtsReady(provider: VideoAiDubbing["provider"]) {
    if (provider !== "vieneu") return;
    let status = await request<VideoAiTtsStatus>("/api/video-ai/tts/prepare", {
      method: "POST",
      body: { provider },
      timeoutMs: 30_000,
    });
    setTtsStatus(status);
    for (let attempt = 0; attempt < 900 && !status.ready; attempt += 1) {
      if (status.state === "failed" || status.state === "not_installed") {
        throw new Error(status.error || status.stage);
      }
      await new Promise((resolve) => window.setTimeout(resolve, 800));
      status = await request<VideoAiTtsStatus>("/api/video-ai/tts/status?provider=vieneu");
      setTtsStatus(status);
    }
    if (!status.ready) throw new Error("VieNeu-TTS tải model quá thời gian chờ.");
  }

  async function previewTtsVoice(options: VideoAiDubbing, text = "") {
    await run("tts-preview", async () => {
      await ensureTtsReady(options.provider);
      const result = await request<{ preview_id: string; voice: string }>(
        "/api/video-ai/tts/preview",
        {
          method: "POST",
          body: {
            provider: options.provider,
            voice: options.voice,
            style: options.style,
            text,
          },
          timeoutMs: 180_000,
        },
      );
      const url = (await window.dyna?.localTtsPreviewUrl(result.preview_id)) || "";
      setTtsPreviewUrl(url ? `${url}&v=${Date.now()}` : "");
      setTtsPreviewVoice(result.voice);
      return result;
    });
  }

  async function saveSubtitles(subtitles: VideoAiSubtitle[], quiet = false): Promise<VideoAiProject | null> {
    if (!project) return null;
    const result = await run("save", async () =>
      request<{ project: VideoAiProject }>(`/api/video-ai/projects/${project.id}/subtitles`, {
        method: "PUT",
        body: { subtitles },
        timeoutMs: 30_000,
      }),
    );
    if (!result) return null;
    setProject(result.project);
    if (!quiet) setMessage(result.project.stage);
    return result.project;
  }

  const saveEditorState = useCallback((payload: VideoAiEditorSaveRequest) => {
    const projectId = project?.id;
    if (!projectId) return Promise.resolve(null);
    const pending = editorSaveQueueRef.current
      .catch(() => null)
      .then(async () => {
        const result = await request<{ project: VideoAiProject }>(
          `/api/video-ai/projects/${projectId}/editor`,
          {
            method: "PUT",
            body: payload,
            timeoutMs: 30_000,
          },
        );
        setProject((current) => (
          current?.id === projectId
            ? {
                ...current,
                dubbing_options: result.project.dubbing_options,
                updated_at: result.project.updated_at,
              }
            : current
        ));
        return result.project;
      });
    editorSaveQueueRef.current = pending;
    return pending;
  }, [project?.id]);

  async function translate(subtitles: VideoAiSubtitle[], targetLanguage: string) {
    if (!project) return;
    const saved = await saveSubtitles(subtitles, true);
    if (!saved) return;
    await run("translate", async () => {
      const result = await request<{ project: VideoAiProject }>(
        `/api/video-ai/projects/${project.id}/translate`,
        {
          method: "POST",
          body: { target_language: targetLanguage },
          timeoutMs: 30_000,
        },
      );
      setProject(result.project);
      return result;
    });
  }

  async function render(subtitles: VideoAiSubtitle[], options: VideoAiRenderRequest) {
    if (!project) return;
    const saved = await saveSubtitles(subtitles, true);
    if (!saved) return;
    await run("render", async () => {
      const result = await request<{ project: VideoAiProject }>(
        `/api/video-ai/projects/${project.id}/render`,
        {
          method: "POST",
          body: options,
          timeoutMs: 30_000,
        },
      );
      setProject(result.project);
      return result;
    });
  }

  async function cancel() {
    if (!project) return;
    await run("cancel", async () => {
      const result = await request<{ project: VideoAiProject }>(
        `/api/video-ai/projects/${project.id}/cancel`,
        { method: "POST", timeoutMs: 15_000 },
      );
      setProject(result.project);
      return result;
    });
  }

  return {
    capabilities,
    project,
    setProject,
    previewUrl,
    dubbingPreviewUrl,
    ttsPreviewUrl,
    ttsPreviewVoice,
    ttsStatus,
    action,
    message,
    error,
    chooseVideo,
    detectSubtitleRegion,
    generateDubbing,
    prepareTts,
    previewTtsVoice,
    transcribe,
    saveSubtitles,
    saveEditorState,
    translate,
    render,
    cancel,
  };
}
