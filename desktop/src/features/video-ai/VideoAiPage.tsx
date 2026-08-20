import {
  AlertTriangle,
  AudioLines,
  Captions,
  CheckCircle2,
  CircleStop,
  FileVideo,
  FolderOpen,
  Gauge,
  HardDrive,
  Headphones,
  Languages,
  ListPlus,
  LoaderCircle,
  Maximize2,
  Pause,
  Play,
  Save,
  ScanSearch,
  Sparkles,
  Trash2,
  Volume2,
  VolumeX,
  WandSparkles,
} from "lucide-react";
import {
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Section, StatusPill } from "../../shared/components/Common";
import { useI18n } from "../../shared/i18n";
import type {
  VideoAiBlur,
  VideoAiDubbing,
  VideoAiStyle,
  VideoAiSubtitle,
} from "../../shared/types";
import { useVideoAi } from "./useVideoAi";
import "./video-ai.css";

const DEFAULT_BLUR: VideoAiBlur = {
  enabled: true,
  x: 0.05,
  y: 0.78,
  width: 0.9,
  height: 0.17,
  strength: 22,
};

const FONT_OPTIONS = ["Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman"];
const MIN_BLUR_WIDTH = 0.05;
const MIN_BLUR_HEIGHT = 0.04;
const MIN_SUBTITLE_DURATION = 0.6;
const MAX_SUBTITLE_CHARACTERS = 84;
const MAX_SUBTITLE_FONT_SIZE = 300;
const AUTOSAVE_DELAY_MS = 700;
const BLUR_HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"] as const;
type BlurDragMode = "move" | (typeof BLUR_HANDLES)[number];
type SubtitleWarning = "invalid" | "overlap" | "short" | "long" | "speed";
type AutosaveState = "idle" | "saving" | "saved" | "error";
type BlurDragState = {
  pointerId: number;
  mode: BlurDragMode;
  startX: number;
  startY: number;
  origin: VideoAiBlur;
};
type EditorDraft = {
  projectId: string;
  updatedAt: number;
  snapshot: string;
  subtitles: VideoAiSubtitle[];
  blur: VideoAiBlur;
  style: VideoAiStyle;
};

const DEFAULT_STYLE: VideoAiStyle = {
  font_name: "Arial",
  font_size: 42,
  margin_v: 54,
  outline: 2,
  primary_color: "&H00FFFFFF",
};
const DEFAULT_DUBBING: VideoAiDubbing = {
  enabled: false,
  provider: "edge",
  voice: "vi-VN-HoaiMyNeural",
  style: "tu_nhien",
  rate: 0,
  volume: 100,
  original_volume: 18,
};

const LANGUAGES = [
  ["auto", "Tự động", "Auto detect", "自动检测"],
  ["vi", "Tiếng Việt", "Vietnamese", "越南语"],
  ["zh", "Tiếng Trung", "Chinese", "中文"],
  ["en", "Tiếng Anh", "English", "英语"],
  ["ja", "Tiếng Nhật", "Japanese", "日语"],
  ["ko", "Tiếng Hàn", "Korean", "韩语"],
  ["th", "Tiếng Thái", "Thai", "泰语"],
  ["id", "Tiếng Indonesia", "Indonesian", "印度尼西亚语"],
] as const;

const BUSY = new Set(["detecting", "transcribing", "translating", "dubbing", "rendering"]);

function fileName(path: string) {
  return path.split(/[\\/]/).pop() || path;
}

function formatDuration(seconds: number) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const remaining = total % 60;
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function formatPlaybackTime(seconds: number) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total % 3600 / 60);
  const remaining = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function readableBytes(bytes: number) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(value / 1024)} KB`;
}

function estimatedSpeechSpeed(subtitle: VideoAiSubtitle) {
  const text = subtitle.translated_text.trim() || subtitle.text.trim();
  const duration = Math.max(0.05, subtitle.end - subtitle.start);
  const characters = Array.from(text.replace(/\s+/g, "")).length;
  const words = text.split(/\s+/).filter(Boolean).length;
  const estimatedDuration = Math.max(characters / 14, words / 2.8);
  return Math.max(1, estimatedDuration / duration);
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value));
}

function roundedRegion(region: VideoAiBlur): VideoAiBlur {
  return {
    ...region,
    x: Number(region.x.toFixed(4)),
    y: Number(region.y.toFixed(4)),
    width: Number(region.width.toFixed(4)),
    height: Number(region.height.toFixed(4)),
  };
}

function editorSnapshot(
  subtitles: VideoAiSubtitle[],
  blur: VideoAiBlur,
  style: VideoAiStyle,
) {
  return JSON.stringify({ subtitles, blur, style });
}

function editorDraftKey(projectId: string) {
  return `dyna.video-ai.editor-draft.${projectId}`;
}

function readEditorDraft(projectId: string): EditorDraft | null {
  try {
    const raw = window.localStorage.getItem(editorDraftKey(projectId));
    if (!raw) return null;
    const draft = JSON.parse(raw) as Partial<EditorDraft>;
    if (
      draft.projectId !== projectId
      || !Array.isArray(draft.subtitles)
      || !draft.blur
      || !draft.style
      || typeof draft.updatedAt !== "number"
      || typeof draft.snapshot !== "string"
    ) {
      return null;
    }
    return draft as EditorDraft;
  } catch {
    return null;
  }
}

function writeEditorDraft(draft: EditorDraft) {
  try {
    window.localStorage.setItem(editorDraftKey(draft.projectId), JSON.stringify(draft));
  } catch {
    // The API autosave remains available if local storage is unavailable.
  }
}

function removeEditorDraft(projectId: string) {
  try {
    window.localStorage.removeItem(editorDraftKey(projectId));
  } catch {
    // A stale local draft is harmless when storage is unavailable.
  }
}

export default function VideoAiPage() {
  const { l } = useI18n();
  const {
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
    translate,
    render,
    cancel,
    saveEditorState,
  } = useVideoAi();
  const videoRef = useRef<HTMLVideoElement>(null);
  const videoPlayerRef = useRef<HTMLDivElement>(null);
  const videoStageRef = useRef<HTMLDivElement>(null);
  const ttsPreviewRef = useRef<HTMLAudioElement>(null);
  const blurDragRef = useRef<BlurDragState | null>(null);
  const segmentEndRef = useRef<number | null>(null);
  const lastSavedSnapshotRef = useRef("");
  const currentEditorSnapshotRef = useRef("");
  const [sourceLanguage, setSourceLanguage] = useState("auto");
  const [targetLanguage, setTargetLanguage] = useState("vi");
  const [modelName, setModelName] = useState("small");
  const [blur, setBlur] = useState<VideoAiBlur>(DEFAULT_BLUR);
  const [style, setStyle] = useState<VideoAiStyle>(DEFAULT_STYLE);
  const [dubbing, setDubbing] = useState<VideoAiDubbing>(DEFAULT_DUBBING);
  const [voiceRegion, setVoiceRegion] = useState("all");
  const [voiceGender, setVoiceGender] = useState("all");
  const [voicePresetStyle, setVoicePresetStyle] = useState("all");
  const [previewTime, setPreviewTime] = useState(0);
  const [previewHeight, setPreviewHeight] = useState(0);
  const [videoDuration, setVideoDuration] = useState(0);
  const [videoPlaying, setVideoPlaying] = useState(false);
  const [videoVolume, setVideoVolume] = useState(1);
  const [videoMuted, setVideoMuted] = useState(false);
  const [videoPlaybackRate, setVideoPlaybackRate] = useState(1);
  const [selectedSubtitleId, setSelectedSubtitleId] = useState("");
  const [editorReadyProjectId, setEditorReadyProjectId] = useState("");
  const [autosaveState, setAutosaveState] = useState<AutosaveState>("idle");

  useEffect(() => {
    if (!project) {
      setEditorReadyProjectId("");
      return;
    }
    const serverBlur = project.render_options?.blur || DEFAULT_BLUR;
    const serverStyle = project.render_options?.style || DEFAULT_STYLE;
    const serverSnapshot = editorSnapshot(project.subtitles, serverBlur, serverStyle);
    const draft = readEditorDraft(project.id);
    const serverUpdatedAt = Date.parse(project.updated_at || "") || 0;
    const useDraft = Boolean(
      draft
      && draft.updatedAt > serverUpdatedAt
      && draft.snapshot !== serverSnapshot,
    );

    setSourceLanguage(project.source_language || "auto");
    setTargetLanguage(project.target_language || "vi");
    setModelName(project.model_name || "small");
    setBlur(useDraft && draft ? draft.blur : serverBlur);
    setStyle(useDraft && draft ? draft.style : serverStyle);
    setDubbing(project.dubbing_options || DEFAULT_DUBBING);
    if (useDraft && draft) {
      setProject({ ...project, subtitles: draft.subtitles });
      setAutosaveState("saving");
    } else {
      removeEditorDraft(project.id);
      setAutosaveState("saved");
    }
    lastSavedSnapshotRef.current = serverSnapshot;
    setSelectedSubtitleId(project.subtitles[0]?.id || "");
    setPreviewTime(0);
    setVideoDuration(project.media.duration_seconds || 0);
    setVideoPlaying(false);
    setEditorReadyProjectId(project.id);
  }, [project?.id]);

  useEffect(() => {
    if (project?.dubbing_options?.generated_at) {
      setDubbing(project.dubbing_options);
    }
  }, [project?.dubbing_options?.generated_at]);

  useEffect(() => {
    if (project?.dubbing_options?.generated_at) return;
    const provider = capabilities?.tts?.providers.find((item) => item.id === dubbing.provider);
    const matchingVoice = provider?.voices.find(
      (voice) => voice.locale.toLowerCase().startsWith(`${targetLanguage.toLowerCase()}-`),
    );
    if (!matchingVoice) return;
    setDubbing((current) => (
      current.voice.toLowerCase().startsWith(`${targetLanguage.toLowerCase()}-`)
        ? current
        : { ...current, voice: matchingVoice.id }
    ));
  }, [
    capabilities?.tts?.providers,
    dubbing.provider,
    project?.dubbing_options?.generated_at,
    targetLanguage,
  ]);

  useEffect(() => {
    const detection = project?.subtitle_detection;
    if (!project || !detection?.detected_at || !detection.detected || !detection.region) {
      return;
    }
    const detectedBlur = detection.region;
    const detectedStyle = project.render_options?.style || style;
    setBlur(detectedBlur);
    setStyle(detectedStyle);
    lastSavedSnapshotRef.current = editorSnapshot(
      project.subtitles,
      detectedBlur,
      detectedStyle,
    );
    removeEditorDraft(project.id);
    setAutosaveState("saved");
  }, [project?.subtitle_detection?.detected_at]);

  useEffect(() => {
    const stage = videoStageRef.current;
    if (!stage) return;
    const updateSize = () => setPreviewHeight(stage.getBoundingClientRect().height);
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [previewUrl, project?.id]);

  const busy = Boolean(project && BUSY.has(project.status));
  const hasTranslations = Boolean(
    project?.subtitles.some((subtitle) => subtitle.translated_text.trim()),
  );
  const statusTone = project?.status === "failed"
    ? "danger"
    : project?.status === "completed"
      ? "success"
      : busy
        ? "info"
        : project?.status === "cancelled"
          ? "warning"
          : "neutral";
  const languageOptions = useMemo(
    () =>
      LANGUAGES.map(([value, vi, en, zh]) => ({
        value,
        label: l(vi, en, zh),
      })),
    [l],
  );
  const previewCaption = useMemo(() => {
    const current = project?.subtitles.find(
      (subtitle) => previewTime >= subtitle.start && previewTime < subtitle.end,
    );
    return current?.translated_text.trim()
      || current?.text.trim()
      || l("Phụ đề dịch sẽ nằm ở đây", "Translated captions appear here", "译文将显示在这里");
  }, [l, previewTime, project?.subtitles]);
  const previewFontSize = project
    ? Math.max(2, style.font_size * previewHeight / Math.max(1, project.media.height))
    : 14;
  const previewMaxHeight = project && project.media.height > project.media.width ? 560 : 420;
  const selectedTtsProvider = capabilities?.tts?.providers.find(
    (provider) => provider.id === dubbing.provider,
  );
  const selectedTtsVoices = selectedTtsProvider?.voices || [];
  const filteredTtsVoices = selectedTtsVoices.filter((voice) => (
    dubbing.provider !== "vieneu"
    || (
      (voiceRegion === "all" || voice.region === voiceRegion)
      && (voiceGender === "all" || voice.gender === voiceGender)
      && (voicePresetStyle === "all" || voice.default_style === voicePresetStyle)
    )
  ));
  useEffect(() => {
    if (!filteredTtsVoices.length) return;
    if (filteredTtsVoices.some((voice) => voice.id === dubbing.voice)) return;
    const voice = filteredTtsVoices[0];
    setDubbing((current) => ({
      ...current,
      voice: voice.id,
      style: voice.default_style || current.style,
    }));
  }, [dubbing.voice, filteredTtsVoices]);
  useEffect(() => {
    if (!ttsPreviewUrl) return;
    void ttsPreviewRef.current?.play().catch(() => undefined);
  }, [ttsPreviewUrl]);
  const dubbingNeedsGeneration = Boolean(
    dubbing.enabled
    && (
      !project?.dubbing_options?.audio_path
      || project.dubbing_options.provider !== dubbing.provider
      || project.dubbing_options.voice !== dubbing.voice
      || project.dubbing_options.style !== dubbing.style
      || project.dubbing_options.rate !== dubbing.rate
    ),
  );
  const currentEditorSnapshot = useMemo(
    () => editorSnapshot(project?.subtitles || [], blur, style),
    [blur, project?.subtitles, style],
  );
  useEffect(() => {
    currentEditorSnapshotRef.current = currentEditorSnapshot;
  }, [currentEditorSnapshot]);
  const subtitleWarnings = useMemo(() => {
    const warnings = new Map<string, Set<SubtitleWarning>>();
    const add = (id: string, warning: SubtitleWarning) => {
      const current = warnings.get(id) || new Set<SubtitleWarning>();
      current.add(warning);
      warnings.set(id, current);
    };
    const subtitles = project?.subtitles || [];
    subtitles.forEach((subtitle) => {
      const duration = subtitle.end - subtitle.start;
      if (duration <= 0) add(subtitle.id, "invalid");
      else if (duration < MIN_SUBTITLE_DURATION) add(subtitle.id, "short");
      const visibleText = subtitle.translated_text.trim() || subtitle.text.trim();
      if (Array.from(visibleText).length > MAX_SUBTITLE_CHARACTERS) add(subtitle.id, "long");
      if (estimatedSpeechSpeed(subtitle) >= 1.35) add(subtitle.id, "speed");
    });
    (project?.dubbing_options?.speed_warnings || []).forEach((warning) => {
      add(warning.id, "speed");
    });
    const ordered = [...subtitles].sort((left, right) => left.start - right.start);
    for (let index = 1; index < ordered.length; index += 1) {
      const previous = ordered[index - 1];
      const current = ordered[index];
      if (current.start < previous.end) {
        add(previous.id, "overlap");
        add(current.id, "overlap");
      }
    }
    return warnings;
  }, [project?.dubbing_options?.speed_warnings, project?.subtitles]);
  const warningCounts = useMemo(() => {
    const counts = { overlap: 0, short: 0, long: 0, invalid: 0, speed: 0 };
    subtitleWarnings.forEach((warnings) => {
      warnings.forEach((warning) => {
        counts[warning] += 1;
      });
    });
    return counts;
  }, [subtitleWarnings]);
  const actualSpeedBySubtitle = useMemo(
    () => new Map(
      (project?.dubbing_options?.speed_warnings || []).map(
        (warning) => [warning.id, warning.speed],
      ),
    ),
    [project?.dubbing_options?.speed_warnings],
  );
  const timelineDuration = Math.max(
    0.1,
    project?.media.duration_seconds || 0,
    ...(project?.subtitles.map((subtitle) => subtitle.end) || [0]),
  );
  const timelineSubtitleId = project?.subtitles.find(
    (subtitle) => previewTime >= subtitle.start && previewTime < subtitle.end,
  )?.id || "";

  useEffect(() => {
    if (
      !project
      || editorReadyProjectId !== project.id
      || busy
    ) {
      return;
    }
    if (currentEditorSnapshot === lastSavedSnapshotRef.current) {
      removeEditorDraft(project.id);
      setAutosaveState("saved");
      return;
    }

    const projectId = project.id;
    const snapshot = currentEditorSnapshot;
    const payload = {
      subtitles: project.subtitles,
      blur,
      style,
    };
    writeEditorDraft({
      projectId,
      updatedAt: Date.now(),
      snapshot,
      ...payload,
    });
    setAutosaveState("saving");
    const timer = window.setTimeout(() => {
      void saveEditorState(payload)
        .then((saved) => {
          if (!saved) return;
          lastSavedSnapshotRef.current = snapshot;
          if (currentEditorSnapshotRef.current === snapshot) {
            removeEditorDraft(projectId);
            setAutosaveState("saved");
          }
        })
        .catch(() => {
          if (currentEditorSnapshotRef.current === snapshot) {
            setAutosaveState("error");
          }
        });
    }, AUTOSAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [
    blur,
    busy,
    currentEditorSnapshot,
    editorReadyProjectId,
    project,
    saveEditorState,
    style,
  ]);

  function updateSubtitle(id: string, changes: Partial<VideoAiSubtitle>) {
    if (!project || busy) return;
    setProject({
      ...project,
      subtitles: project.subtitles.map((subtitle) =>
        subtitle.id === id ? { ...subtitle, ...changes } : subtitle,
      ),
    });
  }

  function insertEmotionTag(tag: string) {
    if (!project || busy) return;
    const subtitle = project.subtitles.find((item) => item.id === selectedSubtitleId)
      || project.subtitles[0];
    if (!subtitle) return;
    const current = subtitle.translated_text.trimEnd();
    updateSubtitle(subtitle.id, {
      translated_text: `${current}${current ? " " : ""}${tag}`,
    });
    setSelectedSubtitleId(subtitle.id);
  }

  function addSubtitle() {
    if (!project || busy) return;
    const previous = project.subtitles.at(-1);
    const start = previous ? previous.end : 0;
    setProject({
      ...project,
      subtitles: [
        ...project.subtitles,
        {
          id: crypto.randomUUID(),
          start: Number(start.toFixed(3)),
          end: Number((start + 2).toFixed(3)),
          text: "",
          translated_text: "",
        },
      ],
    });
  }

  function deleteSubtitle(id: string) {
    if (!project || busy) return;
    setProject({
      ...project,
      subtitles: project.subtitles.filter((subtitle) => subtitle.id !== id),
    });
  }

  function selectSubtitle(subtitle: VideoAiSubtitle) {
    setSelectedSubtitleId(subtitle.id);
    segmentEndRef.current = null;
    if (!videoRef.current) {
      setPreviewTime(subtitle.start);
      return;
    }
    videoRef.current.pause();
    videoRef.current.currentTime = subtitle.start;
    setPreviewTime(subtitle.start);
  }

  function playSubtitle(subtitle: VideoAiSubtitle) {
    if (!videoRef.current) return;
    setSelectedSubtitleId(subtitle.id);
    segmentEndRef.current = subtitle.end;
    videoRef.current.currentTime = subtitle.start;
    void videoRef.current.play();
  }

  function seekTimeline(clientX: number, track: HTMLDivElement) {
    const bounds = track.getBoundingClientRect();
    if (bounds.width <= 0) return;
    const time = clamp((clientX - bounds.left) / bounds.width, 0, 1) * timelineDuration;
    segmentEndRef.current = null;
    setSelectedSubtitleId("");
    setPreviewTime(time);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = time;
    }
  }

  async function saveEditorNow(): Promise<boolean> {
    if (!project || busy) return false;
    const snapshot = currentEditorSnapshotRef.current;
    const payload = { subtitles: project.subtitles, blur, style };
    writeEditorDraft({
      projectId: project.id,
      updatedAt: Date.now(),
      snapshot,
      ...payload,
    });
    setAutosaveState("saving");
    try {
      const saved = await saveEditorState(payload);
      if (!saved) return false;
      lastSavedSnapshotRef.current = snapshot;
      if (currentEditorSnapshotRef.current === snapshot) {
        removeEditorDraft(project.id);
        setAutosaveState("saved");
      }
      return true;
    } catch {
      setAutosaveState("error");
      return false;
    }
  }

  async function runSubtitleDetection() {
    if (!await saveEditorNow()) return;
    await detectSubtitleRegion(24);
  }

  async function runDubbing() {
    if (!await saveEditorNow()) return;
    await generateDubbing({ ...dubbing, enabled: true });
  }

  function updateVideoTime(video: HTMLVideoElement) {
    setPreviewTime(video.currentTime);
    const segmentEnd = segmentEndRef.current;
    if (segmentEnd !== null && video.currentTime >= segmentEnd - 0.03) {
      segmentEndRef.current = null;
      video.pause();
      video.currentTime = segmentEnd;
      setPreviewTime(segmentEnd);
    }
  }

  function toggleVideoPlayback() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused || video.ended) {
      if (video.ended) {
        segmentEndRef.current = null;
        video.currentTime = 0;
      }
      void video.play();
    } else {
      video.pause();
    }
  }

  function seekVideo(time: number) {
    const video = videoRef.current;
    const duration = Math.max(0, videoDuration || project?.media.duration_seconds || 0);
    const nextTime = clamp(time, 0, duration);
    segmentEndRef.current = null;
    setPreviewTime(nextTime);
    if (video) video.currentTime = nextTime;
  }

  function changeVideoVolume(volume: number) {
    const nextVolume = clamp(volume, 0, 1);
    setVideoVolume(nextVolume);
    setVideoMuted(nextVolume === 0);
    if (videoRef.current) {
      videoRef.current.volume = nextVolume;
      videoRef.current.muted = nextVolume === 0;
    }
  }

  function toggleVideoMute() {
    const muted = !videoMuted;
    setVideoMuted(muted);
    if (videoRef.current) videoRef.current.muted = muted;
  }

  function changeVideoPlaybackRate(rate: number) {
    const nextRate = clamp(rate, 0.5, 2);
    setVideoPlaybackRate(nextRate);
    if (videoRef.current) videoRef.current.playbackRate = nextRate;
  }

  async function toggleVideoFullscreen() {
    const player = videoPlayerRef.current;
    if (!player) return;
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await player.requestFullscreen();
    }
  }

  function startBlurDrag(
    event: ReactPointerEvent<HTMLElement>,
    mode: BlurDragMode,
  ) {
    if (busy || !blur.enabled) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    blurDragRef.current = {
      pointerId: event.pointerId,
      mode,
      startX: event.clientX,
      startY: event.clientY,
      origin: { ...blur },
    };
  }

  function moveBlurRegion(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = blurDragRef.current;
    const stage = videoStageRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !stage) return;
    event.preventDefault();
    const bounds = stage.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    const dx = (event.clientX - drag.startX) / bounds.width;
    const dy = (event.clientY - drag.startY) / bounds.height;
    const next = { ...drag.origin };

    if (drag.mode === "move") {
      next.x = clamp(drag.origin.x + dx, 0, 1 - drag.origin.width);
      next.y = clamp(drag.origin.y + dy, 0, 1 - drag.origin.height);
    } else {
      if (drag.mode.includes("w")) {
        const right = drag.origin.x + drag.origin.width;
        next.x = clamp(drag.origin.x + dx, 0, right - MIN_BLUR_WIDTH);
        next.width = right - next.x;
      }
      if (drag.mode.includes("e")) {
        next.width = clamp(
          drag.origin.width + dx,
          MIN_BLUR_WIDTH,
          1 - drag.origin.x,
        );
      }
      if (drag.mode.includes("n")) {
        const bottom = drag.origin.y + drag.origin.height;
        next.y = clamp(drag.origin.y + dy, 0, bottom - MIN_BLUR_HEIGHT);
        next.height = bottom - next.y;
      }
      if (drag.mode.includes("s")) {
        next.height = clamp(
          drag.origin.height + dy,
          MIN_BLUR_HEIGHT,
          1 - drag.origin.y,
        );
      }
    }
    setBlur(roundedRegion(next));
  }

  function finishBlurDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (blurDragRef.current?.pointerId === event.pointerId) {
      blurDragRef.current = null;
    }
  }

  return (
    <div className="page-stack video-ai-page">
      <div className="video-ai-intro">
        <span className="video-ai-intro-icon">
          <Sparkles size={21} />
        </span>
        <div>
          <strong>{l("Xử lý video AI", "AI Video Processing", "AI 视频处理")}</strong>
          <span>
            {l(
              "Tạo, dịch và chỉnh sửa phụ đề; làm mờ phụ đề cũ rồi xuất video mới.",
              "Generate, translate and edit subtitles; blur old captions and export a new video.",
              "生成、翻译和编辑字幕；模糊旧字幕并导出新视频。",
            )}
          </span>
        </div>
        {project && <StatusPill text={project.stage} tone={statusTone} />}
      </div>

      {capabilities && (!capabilities.ffmpeg.ready || !capabilities.asr.ready) && (
        <div className="video-ai-dependency-warning">
          <AlertTriangle size={18} />
          <div>
            <strong>
              {!capabilities.ffmpeg.ready
                ? l("Chưa tìm thấy FFmpeg", "FFmpeg is unavailable", "未找到 FFmpeg")
                : l(
                    "Chưa cài bộ nhận dạng faster-whisper",
                    "faster-whisper is not installed",
                    "尚未安装 faster-whisper",
                  )}
            </strong>
            <span>
              {!capabilities.ffmpeg.ready
                ? l(
                    "Cài FFmpeg vào PATH hoặc C:/ffmpeg/bin trước khi xuất video.",
                    "Install FFmpeg in PATH or C:/ffmpeg/bin before rendering.",
                    "导出视频前，请将 FFmpeg 安装到 PATH 或 C:/ffmpeg/bin。",
                  )
                : `${l("Chạy lệnh", "Run", "运行")} ${capabilities.asr.install_command}`}
            </span>
          </div>
        </div>
      )}

      {(error || message) && (
        <div className={`video-ai-notice ${error ? "error" : "success"}`}>
          {error ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
          <span>{error || message}</span>
        </div>
      )}

      <Section
        title={`1. ${l("Chọn video nguồn", "Choose source video", "选择源视频")}`}
        action={
          <button className="small-button primary" disabled={busy || action === "select"} onClick={() => void chooseVideo()}>
            {action === "select" ? <LoaderCircle size={14} className="spin" /> : <FolderOpen size={14} />}
            {l("Chọn video", "Choose video", "选择视频")}
          </button>
        }
      >
        {!project ? (
          <button className="video-ai-dropzone" disabled={action === "select"} onClick={() => void chooseVideo()}>
            <FileVideo size={31} />
            <strong>{l("Chọn một video để bắt đầu", "Choose a video to begin", "选择一个视频开始")}</strong>
            <span>MP4, M4V, MOV, WEBM</span>
          </button>
        ) : (
          <div className="video-ai-source">
            <div className="video-ai-source-info">
              <div className="video-ai-source-title">
                <strong title={project.source_path}>{fileName(project.source_path)}</strong>
                <span>{project.source_path}</span>
              </div>
              <dl>
                <div>
                  <dt>{l("Thời lượng", "Duration", "时长")}</dt>
                  <dd>{formatDuration(project.media.duration_seconds)}</dd>
                </div>
                <div>
                  <dt>{l("Kích thước", "Resolution", "分辨率")}</dt>
                  <dd>{project.media.width}×{project.media.height}</dd>
                </div>
                <div>
                  <dt>{l("Dung lượng", "File size", "文件大小")}</dt>
                  <dd>{readableBytes(project.media.file_size)}</dd>
                </div>
                <div>
                  <dt>{l("Âm thanh", "Audio", "音频")}</dt>
                  <dd>{project.media.has_audio ? project.media.audio_codec || "Có" : l("Không có", "None", "无")}</dd>
                </div>
              </dl>
            </div>
            <div ref={videoPlayerRef} className="video-ai-preview">
              {previewUrl ? (
                <>
                  <div className="video-ai-preview-viewport">
                    <div
                      ref={videoStageRef}
                      className="video-ai-stage"
                      style={{
                        aspectRatio: `${project.media.width} / ${project.media.height}`,
                        maxWidth: `${previewMaxHeight * project.media.width / Math.max(1, project.media.height)}px`,
                      }}
                    >
                      <video
                        ref={videoRef}
                        src={previewUrl}
                        preload="metadata"
                        onClick={toggleVideoPlayback}
                        onDurationChange={(event) => setVideoDuration(event.currentTarget.duration || project.media.duration_seconds)}
                        onEnded={() => setVideoPlaying(false)}
                        onLoadedMetadata={(event) => {
                          const video = event.currentTarget;
                          video.volume = videoVolume;
                          video.muted = videoMuted;
                          video.playbackRate = videoPlaybackRate;
                          setVideoDuration(video.duration || project.media.duration_seconds);
                        }}
                        onPause={() => setVideoPlaying(false)}
                        onPlay={() => setVideoPlaying(true)}
                        onTimeUpdate={(event) => updateVideoTime(event.currentTarget)}
                        onSeeked={(event) => updateVideoTime(event.currentTarget)}
                      />
                      {blur.enabled && (
                        <div
                          className={`video-ai-blur-overlay${busy ? " disabled" : ""}`}
                          style={{
                            left: `${blur.x * 100}%`,
                            top: `${blur.y * 100}%`,
                            width: `${blur.width * 100}%`,
                            height: `${blur.height * 100}%`,
                            backdropFilter: `blur(${clamp(blur.strength / 3, 1, 20)}px)`,
                          }}
                          onPointerDown={(event) => startBlurDrag(event, "move")}
                          onPointerMove={moveBlurRegion}
                          onPointerUp={finishBlurDrag}
                          onPointerCancel={finishBlurDrag}
                        >
                          <span
                            className="video-ai-caption-preview"
                            style={{
                              fontFamily: style.font_name,
                              fontSize: `${previewFontSize}px`,
                            }}
                          >
                            {previewCaption}
                          </span>
                          {BLUR_HANDLES.map((handle) => (
                            <i
                              aria-hidden="true"
                              className={`video-ai-resize-handle ${handle}`}
                              key={handle}
                              onPointerDown={(event) => startBlurDrag(event, handle)}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="video-ai-player-controls">
                    <div className="video-ai-player-toolbar">
                      <button
                        type="button"
                        onClick={toggleVideoPlayback}
                        title={videoPlaying ? l("Tạm dừng", "Pause", "暂停") : l("Phát", "Play", "播放")}
                      >
                        {videoPlaying ? <Pause size={16} /> : <Play size={16} />}
                      </button>
                      <span className="video-ai-player-time">
                        {formatPlaybackTime(previewTime)}
                        <i>/</i>
                        {formatPlaybackTime(videoDuration || project.media.duration_seconds)}
                      </span>
                      <div className="video-ai-player-volume">
                        <button
                          type="button"
                          onClick={toggleVideoMute}
                          title={videoMuted ? l("Bật tiếng", "Unmute", "取消静音") : l("Tắt tiếng", "Mute", "静音")}
                        >
                          {videoMuted || videoVolume === 0 ? <VolumeX size={16} /> : <Volume2 size={16} />}
                        </button>
                        <input
                          aria-label={l("Âm lượng video", "Video volume", "视频音量")}
                          type="range"
                          min={0}
                          max={1}
                          step={0.05}
                          value={videoMuted ? 0 : videoVolume}
                          onChange={(event) => changeVideoVolume(Number(event.target.value))}
                        />
                      </div>
                      <select
                        aria-label={l("Tốc độ phát", "Playback speed", "播放速度")}
                        value={videoPlaybackRate}
                        onChange={(event) => changeVideoPlaybackRate(Number(event.target.value))}
                      >
                        {[0.5, 0.75, 1, 1.25, 1.5, 2].map((rate) => (
                          <option key={rate} value={rate}>{rate}×</option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => void toggleVideoFullscreen()}
                        title={l("Toàn màn hình", "Fullscreen", "全屏")}
                      >
                        <Maximize2 size={16} />
                      </button>
                    </div>
                    <input
                      className="video-ai-player-timeline"
                      aria-label={l("Vị trí phát video", "Video position", "视频播放位置")}
                      type="range"
                      min={0}
                      max={Math.max(0.01, videoDuration || project.media.duration_seconds)}
                      step={0.01}
                      value={clamp(previewTime, 0, Math.max(0.01, videoDuration || project.media.duration_seconds))}
                      style={{
                        background: `linear-gradient(to right, var(--accent) ${clamp(previewTime / Math.max(0.01, videoDuration || project.media.duration_seconds) * 100, 0, 100)}%, #404754 0%)`,
                      }}
                      onChange={(event) => seekVideo(Number(event.target.value))}
                    />
                  </div>
                </>
              ) : (
                <div className="video-ai-preview-empty">
                  <FileVideo size={32} />
                  {l("Không mở được bản xem trước", "Preview unavailable", "无法预览")}
                </div>
              )}
            </div>
          </div>
        )}
      </Section>

      {project && (
        <>
          {busy && (
            <div className="video-ai-progress-card">
              <div>
                <LoaderCircle size={17} className="spin" />
                <strong>{project.stage}</strong>
                <span>{project.progress}%</span>
              </div>
              <div className="video-ai-progress-track">
                <i style={{ width: `${project.progress}%` }} />
              </div>
              <button className="small-button danger" disabled={action === "cancel"} onClick={() => void cancel()}>
                <CircleStop size={14} />
                {l("Dừng tác vụ", "Stop task", "停止任务")}
              </button>
            </div>
          )}

          <div className="video-ai-workflow-grid">
            <Section title={`2. ${l("Nhận dạng lời nói", "Speech recognition", "语音识别")}`}>
              <div className="video-ai-step-form">
                <label>
                  <span>{l("Ngôn ngữ đang nói", "Spoken language", "语音语言")}</span>
                  <select value={sourceLanguage} disabled={busy} onChange={(event) => setSourceLanguage(event.target.value)}>
                    {languageOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                  </select>
                </label>
                <label>
                  <span>{l("Model nhận dạng", "Recognition model", "识别模型")}</span>
                  <select value={modelName} disabled={busy} onChange={(event) => setModelName(event.target.value)}>
                    {(capabilities?.asr.models || ["tiny", "base", "small", "medium", "large-v3"]).map((model) => (
                      <option key={model} value={model}>{model}</option>
                    ))}
                  </select>
                  <small>
                    {l(
                      `${modelName} sẽ được tải ở lần chạy đầu tiên.`,
                      `${modelName} is downloaded on its first run.`,
                      `${modelName} 将在首次运行时下载。`,
                    )}
                  </small>
                  {capabilities?.asr.device === "cpu" && capabilities.asr.cuda_devices > 0 && (
                    <small className="video-ai-device-note">
                      {l(
                        `GPU đã được phát hiện nhưng thiếu ${capabilities.asr.cuda_missing.join(", ")}; Dyna sẽ tự dùng CPU.`,
                        `A GPU was detected but ${capabilities.asr.cuda_missing.join(", ")} is missing; Dyna will use the CPU.`,
                        `检测到 GPU，但缺少 ${capabilities.asr.cuda_missing.join(", ")}；Dyna 将自动使用 CPU。`,
                      )}
                    </small>
                  )}
                </label>
                <button
                  className="primary-button"
                  disabled={busy || !capabilities?.asr.ready || !project.media.has_audio}
                  onClick={() => void transcribe(sourceLanguage, modelName)}
                >
                  <Captions size={15} />
                  {project.subtitles.length
                    ? l("Nhận dạng lại", "Transcribe again", "重新识别")
                    : l("Tạo phụ đề", "Generate subtitles", "生成字幕")}
                </button>
                {project.detected_language && (
                  <small className="video-ai-detected">
                    {l("AI phát hiện", "Detected", "检测到")}: {project.detected_language}
                  </small>
                )}
              </div>
            </Section>

            <Section title={`3. ${l("Dịch phụ đề", "Translate subtitles", "翻译字幕")}`}>
              <div className="video-ai-step-form">
                <label>
                  <span>{l("Ngôn ngữ đích", "Target language", "目标语言")}</span>
                  <select value={targetLanguage} disabled={busy} onChange={(event) => setTargetLanguage(event.target.value)}>
                    {languageOptions.filter((item) => item.value !== "auto").map((item) => (
                      <option key={item.value} value={item.value}>{item.label}</option>
                    ))}
                  </select>
                </label>
                <div className="video-ai-translation-summary">
                  <Languages size={20} />
                  <span>
                    <strong>{project.subtitles.length}</strong>
                    {l(" dòng nguồn", " source lines", " 条源字幕")}
                  </span>
                  <span>
                    <strong>{project.subtitles.filter((item) => item.translated_text.trim()).length}</strong>
                    {l(" dòng đã dịch", " translated lines", " 条已翻译")}
                  </span>
                </div>
                <button
                  className="primary-button"
                  disabled={busy || !project.subtitles.length || !capabilities?.translation.ready}
                  onClick={() => void translate(project.subtitles, targetLanguage)}
                >
                  <WandSparkles size={15} />
                  {hasTranslations
                    ? l("Dịch lại bằng DynaAI", "Translate again with DynaAI", "使用 DynaAI 重新翻译")
                    : l("Dịch bằng DynaAI", "Translate with DynaAI", "使用 DynaAI 翻译")}
                </button>
              </div>
            </Section>
          </div>

          <Section
            title={`4. ${l("Chỉnh sửa phụ đề", "Edit subtitles", "编辑字幕")} · ${project.subtitles.length}`}
            className="video-ai-editor-section"
            action={
              <div className="inline-actions">
                <span className={`video-ai-autosave ${autosaveState}`}>
                  {autosaveState === "saving" ? (
                    <LoaderCircle size={13} className="spin" />
                  ) : autosaveState === "error" ? (
                    <AlertTriangle size={13} />
                  ) : (
                    <CheckCircle2 size={13} />
                  )}
                  {autosaveState === "saving"
                    ? l("Đang tự lưu", "Autosaving", "正在自动保存")
                    : autosaveState === "error"
                      ? l("Chưa thể tự lưu", "Autosave failed", "自动保存失败")
                      : l("Đã tự lưu", "Autosaved", "已自动保存")}
                </span>
                <button className="small-button" disabled={busy} onClick={addSubtitle}>
                  <ListPlus size={14} />
                  {l("Thêm dòng", "Add line", "添加一行")}
                </button>
                <button
                  className="small-button primary"
                  disabled={busy || autosaveState === "saving"}
                  onClick={() => void saveEditorNow()}
                >
                  <Save size={14} />
                  {l("Lưu ngay", "Save now", "立即保存")}
                </button>
              </div>
            }
          >
            {!project.subtitles.length ? (
              <div className="video-ai-editor-empty">
                <Captions size={25} />
                <strong>{l("Chưa có phụ đề", "No subtitles yet", "暂无字幕")}</strong>
                <span>
                  {l(
                    "Hãy chạy nhận dạng lời nói hoặc thêm dòng thủ công.",
                    "Run speech recognition or add a line manually.",
                    "请运行语音识别或手动添加字幕。",
                  )}
                </span>
              </div>
            ) : (
              <div className="video-ai-editor-workspace">
                <div className="video-ai-timeline">
                  <div className="video-ai-timeline-title">
                    <strong>{l("Timeline phụ đề", "Subtitle timeline", "字幕时间轴")}</strong>
                    <span>{formatDuration(timelineDuration)}</span>
                  </div>
                  <div className="video-ai-timeline-axis" aria-hidden="true">
                    {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
                      <span key={ratio} style={{ left: `${ratio * 100}%` }}>
                        {formatDuration(timelineDuration * ratio)}
                      </span>
                    ))}
                  </div>
                  <div
                    className="video-ai-timeline-track"
                    onClick={(event) => seekTimeline(event.clientX, event.currentTarget)}
                  >
                    {project.subtitles.map((subtitle, index) => {
                      const left = clamp(subtitle.start / timelineDuration * 100, 0, 100);
                      const width = Math.max(
                        0.6,
                        (subtitle.end - subtitle.start) / timelineDuration * 100,
                      );
                      const active = subtitle.id === selectedSubtitleId
                        || subtitle.id === timelineSubtitleId;
                      const hasWarning = Boolean(subtitleWarnings.get(subtitle.id)?.size);
                      return (
                        <button
                          type="button"
                          key={subtitle.id}
                          className={`video-ai-timeline-segment${active ? " active" : ""}${hasWarning ? " warning" : ""}`}
                          style={{ left: `${left}%`, width: `${width}%` }}
                          title={`${index + 1}. ${formatDuration(subtitle.start)} → ${formatDuration(subtitle.end)}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            selectSubtitle(subtitle);
                          }}
                        >
                          {index + 1}
                        </button>
                      );
                    })}
                    <i
                      className="video-ai-timeline-playhead"
                      style={{ left: `${clamp(previewTime / timelineDuration * 100, 0, 100)}%` }}
                    />
                  </div>
                </div>
                <div className="video-ai-quality-summary">
                  {Object.values(warningCounts).every((count) => count === 0) ? (
                    <span className="good">
                      <CheckCircle2 size={13} />
                      {l("Không phát hiện vấn đề", "No issues detected", "未发现问题")}
                    </span>
                  ) : (
                    <>
                      {warningCounts.overlap > 0 && <span>{warningCounts.overlap} {l("dòng chồng thời gian", "overlapping lines", "条时间重叠")}</span>}
                      {warningCounts.short > 0 && <span>{warningCounts.short} {l("dòng quá ngắn", "short lines", "条过短")}</span>}
                      {warningCounts.long > 0 && <span>{warningCounts.long} {l("dòng quá dài", "long lines", "条过长")}</span>}
                      {warningCounts.speed > 0 && <span>{warningCounts.speed} {l("dòng có nguy cơ bị ép tốc độ", "lines may be time-compressed", "条可能被加速")}</span>}
                      {warningCounts.invalid > 0 && <span>{warningCounts.invalid} {l("dòng sai thời gian", "invalid timings", "条时间无效")}</span>}
                    </>
                  )}
                </div>
                <div className="video-ai-subtitle-table">
                  <div className="video-ai-subtitle-head">
                    <span>#</span>
                    <span>{l("Thời gian", "Timing", "时间")}</span>
                    <span>{l("Nội dung gốc", "Source text", "原文")}</span>
                    <span>{l("Bản dịch", "Translation", "译文")}</span>
                    <span>{l("Kiểm tra", "Checks", "检查")}</span>
                    <span />
                  </div>
                  {project.subtitles.map((subtitle, index) => {
                    const warnings = subtitleWarnings.get(subtitle.id) || new Set<SubtitleWarning>();
                    const active = subtitle.id === selectedSubtitleId || subtitle.id === timelineSubtitleId;
                    return (
                      <div
                        className={`video-ai-subtitle-row${active ? " active" : ""}`}
                        key={subtitle.id}
                        onFocusCapture={() => setSelectedSubtitleId(subtitle.id)}
                      >
                        <button className="video-ai-play-line" onClick={() => playSubtitle(subtitle)} title={l("Phát đoạn này", "Play this segment", "播放此片段")}>
                          <Play size={12} />
                          {index + 1}
                        </button>
                        <div className="video-ai-timing">
                          <input
                            type="number"
                            min={0}
                            step={0.1}
                            value={subtitle.start}
                            disabled={busy}
                            onChange={(event) => updateSubtitle(subtitle.id, { start: Number(event.target.value) })}
                          />
                          <span>→</span>
                          <input
                            type="number"
                            min={0.05}
                            step={0.1}
                            value={subtitle.end}
                            disabled={busy}
                            onChange={(event) => updateSubtitle(subtitle.id, { end: Number(event.target.value) })}
                          />
                        </div>
                        <textarea
                          rows={2}
                          value={subtitle.text}
                          disabled={busy}
                          onChange={(event) => updateSubtitle(subtitle.id, { text: event.target.value })}
                        />
                        <textarea
                          rows={2}
                          value={subtitle.translated_text}
                          disabled={busy}
                          placeholder={l("Chưa dịch", "Not translated", "未翻译")}
                          onChange={(event) => updateSubtitle(subtitle.id, { translated_text: event.target.value })}
                        />
                        <div className="video-ai-row-warnings">
                          {!warnings.size && (
                            <span className="good">{l("Tốt", "Good", "正常")}</span>
                          )}
                          {warnings.has("invalid") && <span>{l("Sai thời gian", "Invalid timing", "时间无效")}</span>}
                          {warnings.has("overlap") && <span>{l("Chồng thời gian", "Overlap", "时间重叠")}</span>}
                          {warnings.has("short") && <span>{l("Quá ngắn", "Too short", "过短")}</span>}
                          {warnings.has("long") && <span>{l("Quá 84 ký tự", "Over 84 characters", "超过84个字符")}</span>}
                          {warnings.has("speed") && (
                            <span>
                              <Gauge size={11} />
                              {l(
                                `Có thể bị ép ~${(actualSpeedBySubtitle.get(subtitle.id) || estimatedSpeechSpeed(subtitle)).toFixed(1)}x`,
                                `May be compressed ~${(actualSpeedBySubtitle.get(subtitle.id) || estimatedSpeechSpeed(subtitle)).toFixed(1)}x`,
                                `可能加速约 ${(actualSpeedBySubtitle.get(subtitle.id) || estimatedSpeechSpeed(subtitle)).toFixed(1)}x`,
                              )}
                            </span>
                          )}
                        </div>
                        <button className="icon-button danger" disabled={busy} onClick={() => deleteSubtitle(subtitle.id)} title={l("Xóa dòng", "Delete line", "删除此行")}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </Section>

          <Section title={`5. ${l("Lồng tiếng", "Dubbing", "配音")}`}>
            <div className="video-ai-dubbing">
              <div className="video-ai-dubbing-heading">
                <AudioLines size={21} />
                <div>
                  <strong>{l("Tạo giọng đọc từ phụ đề dịch", "Create speech from translated subtitles", "从翻译字幕生成语音")}</strong>
                  <span>
                    {l(
                      dubbing.provider === "vieneu"
                        ? "VieNeu-TTS chạy offline bằng CPU. Lần đầu sẽ tải model khoảng 300 MB; mỗi câu vẫn được căn đúng timeline."
                        : "Edge-TTS cần Internet. Mỗi câu được căn đúng thời gian; câu dài sẽ tự tăng tốc để không chồng sang câu sau.",
                      dubbing.provider === "vieneu"
                        ? "VieNeu-TTS runs offline on CPU. The first run downloads about 300 MB; every line stays aligned to the timeline."
                        : "Edge-TTS requires Internet. Each line is aligned to its timing; long speech is sped up to avoid overlapping the next line.",
                      dubbing.provider === "vieneu"
                        ? "VieNeu-TTS 使用 CPU 离线运行。首次使用会下载约 300 MB 模型；每句仍与时间轴对齐。"
                        : "Edge-TTS 需要联网。每句语音会与时间轴对齐；过长语音会自动加速以避免重叠。",
                    )}
                  </span>
                </div>
                <label className="switch-row">
                  <input
                    type="checkbox"
                    checked={dubbing.enabled}
                    disabled={busy}
                    onChange={(event) =>
                      setDubbing((current) => ({ ...current, enabled: event.target.checked }))
                    }
                  />
                  <span>{l("Dùng khi xuất", "Use in export", "导出时使用")}</span>
                </label>
              </div>
              {dubbing.provider === "vieneu" && (
                <div className="video-ai-voice-filters">
                  <strong>{l("Lọc giọng VieNeu", "Filter VieNeu voices", "筛选 VieNeu 语音")}</strong>
                  <label>
                    <span>{l("Vùng miền", "Region", "地区")}</span>
                    <select value={voiceRegion} onChange={(event) => setVoiceRegion(event.target.value)} disabled={busy}>
                      <option value="all">{l("Tất cả", "All", "全部")}</option>
                      <option value="Bắc">{l("Miền Bắc", "Northern", "北部")}</option>
                      <option value="Nam">{l("Miền Nam", "Southern", "南部")}</option>
                      <option value="Trung">{l("Miền Trung", "Central", "中部")}</option>
                    </select>
                  </label>
                  <label>
                    <span>{l("Giới tính", "Gender", "性别")}</span>
                    <select value={voiceGender} onChange={(event) => setVoiceGender(event.target.value)} disabled={busy}>
                      <option value="all">{l("Tất cả", "All", "全部")}</option>
                      <option value="female">{l("Nữ", "Female", "女")}</option>
                      <option value="male">{l("Nam", "Male", "男")}</option>
                    </select>
                  </label>
                  <label>
                    <span>{l("Chất giọng gốc", "Preset style", "预设风格")}</span>
                    <select value={voicePresetStyle} onChange={(event) => setVoicePresetStyle(event.target.value)} disabled={busy}>
                      <option value="all">{l("Tất cả", "All", "全部")}</option>
                      <option value="tu_nhien">{l("Tự nhiên", "Natural", "自然")}</option>
                      <option value="tin_tuc">{l("Tin tức", "News", "新闻")}</option>
                      <option value="doc_truyen">{l("Kể chuyện", "Storytelling", "讲故事")}</option>
                    </select>
                  </label>
                  <span className="video-ai-voice-filter-count">
                    {filteredTtsVoices.length}/14 {l("giọng", "voices", "个语音")}
                  </span>
                </div>
              )}
              <div className="video-ai-dubbing-controls">
                <label>
                  <span>{l("Công cụ giọng đọc", "Speech engine", "语音引擎")}</span>
                  <select
                    value={dubbing.provider}
                    disabled={busy}
                    onChange={(event) => {
                      const providerId = event.target.value as VideoAiDubbing["provider"];
                      const provider = capabilities?.tts?.providers.find(
                        (item) => item.id === providerId,
                      );
                      const voice = provider?.voices[0];
                      setDubbing((current) => ({
                        ...current,
                        enabled: true,
                        provider: providerId,
                        voice: voice?.id || current.voice,
                        style: voice?.default_style || "tu_nhien",
                      }));
                    }}
                  >
                    {(capabilities?.tts?.providers || []).map((provider) => (
                      <option key={provider.id} value={provider.id} disabled={!provider.ready}>
                        {provider.name} · {provider.offline
                          ? l("Offline", "Offline", "离线")
                          : l("Online", "Online", "在线")}
                        {!provider.ready ? ` · ${l("chưa cài", "not installed", "未安装")}` : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>{l("Giọng đọc", "Voice", "语音")}</span>
                  <div className="video-ai-voice-select">
                    <select
                      value={dubbing.voice}
                      disabled={busy}
                      onChange={(event) =>
                        setDubbing((current) => ({
                          ...current,
                          enabled: true,
                          voice: event.target.value,
                          style: selectedTtsVoices.find((voice) => voice.id === event.target.value)
                            ?.default_style || current.style,
                        }))
                      }
                    >
                      {filteredTtsVoices.map((voice) => (
                        <option key={voice.id} value={voice.id}>
                          {voice.name}{voice.region ? ` · ${voice.region}` : ` · ${voice.locale}`} · {voice.gender === "female"
                            ? l("Nữ", "Female", "女")
                            : l("Nam", "Male", "男")}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="small-button"
                      disabled={busy || action === "tts-preview" || !selectedTtsProvider?.ready}
                      onClick={() => {
                        const selectedText = project.subtitles.find(
                          (subtitle) => subtitle.id === selectedSubtitleId,
                        )?.translated_text || "";
                        void previewTtsVoice(dubbing, selectedText.slice(0, 300));
                      }}
                      title={l("Nghe thử giọng đang chọn", "Preview selected voice", "试听所选语音")}
                    >
                      {action === "tts-preview"
                        ? <LoaderCircle size={14} className="spin" />
                        : <Headphones size={14} />}
                      {l("Nghe thử", "Preview", "试听")}
                    </button>
                  </div>
                </label>
                {dubbing.provider === "vieneu" && (
                  <label>
                    <span>{l("Phong cách đọc", "Reading style", "朗读风格")}</span>
                    <select
                      value={dubbing.style}
                      disabled={busy}
                      onChange={(event) =>
                        setDubbing((current) => ({
                          ...current,
                          enabled: true,
                          style: event.target.value as VideoAiDubbing["style"],
                        }))
                      }
                    >
                      {(selectedTtsProvider?.styles || []).map((styleOption) => (
                        <option key={styleOption.id} value={styleOption.id}>
                          {styleOption.id === "tu_nhien"
                            ? l("Tự nhiên / hội thoại", "Natural / conversational", "自然 / 对话")
                            : styleOption.id === "tin_tuc"
                              ? l("Tin tức", "News", "新闻")
                              : l("Kể chuyện", "Storytelling", "讲故事")}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label>
                  <span>{l("Tốc độ giọng", "Voice speed", "语速")}</span>
                  <div className="video-ai-dubbing-range">
                    <input
                      type="range"
                      min={-50}
                      max={50}
                      step={5}
                      value={dubbing.rate}
                      disabled={busy}
                      onChange={(event) =>
                        setDubbing((current) => ({
                          ...current,
                          enabled: true,
                          rate: Number(event.target.value),
                        }))
                      }
                    />
                    <output>{dubbing.rate >= 0 ? "+" : ""}{dubbing.rate}%</output>
                  </div>
                </label>
                <label>
                  <span>{l("Âm lượng lồng tiếng", "Dubbing volume", "配音音量")}</span>
                  <div className="video-ai-dubbing-range">
                    <input
                      type="range"
                      min={0}
                      max={200}
                      step={5}
                      value={dubbing.volume}
                      disabled={busy}
                      onChange={(event) =>
                        setDubbing((current) => ({
                          ...current,
                          volume: Number(event.target.value),
                        }))
                      }
                    />
                    <output>{dubbing.volume}%</output>
                  </div>
                </label>
                <label>
                  <span>{l("Âm thanh gốc", "Original audio", "原声音量")}</span>
                  <div className="video-ai-dubbing-range">
                    <input
                      type="range"
                      min={0}
                      max={100}
                      step={5}
                      value={dubbing.original_volume}
                      disabled={busy || !project.media.has_audio}
                      onChange={(event) =>
                        setDubbing((current) => ({
                          ...current,
                          original_volume: Number(event.target.value),
                        }))
                      }
                    />
                    <output>{dubbing.original_volume}%</output>
                  </div>
                </label>
              </div>
              {dubbing.provider === "vieneu" && ttsStatus && (
                <div className={`video-ai-model-status ${ttsStatus.state}`}>
                  <HardDrive size={18} />
                  <div className="video-ai-model-status-body">
                    <div>
                      <strong>{ttsStatus.stage}</strong>
                      <span>
                        {readableBytes(ttsStatus.size_bytes)}
                        {ttsStatus.expected_size_bytes > 0
                          ? ` / ~${readableBytes(ttsStatus.expected_size_bytes)}`
                          : ""}
                      </span>
                    </div>
                    <div className="video-ai-model-progress" aria-label={`${ttsStatus.progress}%`}>
                      <span style={{ width: `${ttsStatus.progress}%` }} />
                    </div>
                    <code title={ttsStatus.cache_path}>{ttsStatus.cache_path}</code>
                    {ttsStatus.error && <small>{ttsStatus.error}</small>}
                  </div>
                  {!ttsStatus.ready && (
                    <button
                      type="button"
                      className="small-button primary"
                      disabled={action === "tts-prepare" || ttsStatus.state === "loading"}
                      onClick={() => void prepareTts("vieneu")}
                    >
                      {ttsStatus.state === "loading" || action === "tts-prepare"
                        ? <LoaderCircle size={14} className="spin" />
                        : <HardDrive size={14} />}
                      {ttsStatus.state === "downloaded"
                        ? l("Nạp model", "Load model", "加载模型")
                        : ttsStatus.state === "failed"
                          ? l("Thử lại", "Retry", "重试")
                          : l("Tải model", "Download model", "下载模型")}
                    </button>
                  )}
                </div>
              )}
              {ttsPreviewUrl && (
                <div className="video-ai-voice-preview">
                  <Headphones size={16} />
                  <strong>{ttsPreviewVoice}</strong>
                  <audio ref={ttsPreviewRef} controls preload="auto" src={ttsPreviewUrl} />
                </div>
              )}
              {dubbing.provider === "vieneu" && (
                <div className="video-ai-emotion-tools">
                  <div>
                    <strong>{l("Thẻ cảm xúc thử nghiệm", "Experimental emotion tags", "实验性情感标签")}</strong>
                    <span>
                      {l(
                        "Chọn một dòng phụ đề rồi chèn thẻ vào bản dịch. VieNeu sẽ tạo cả âm thanh biểu cảm.",
                        "Select a subtitle line, then insert a tag into its translation. VieNeu will synthesize the expression.",
                        "选择一条字幕，然后在译文中插入标签。VieNeu 会合成相应的情感声音。",
                      )}
                    </span>
                  </div>
                  <div className="video-ai-emotion-buttons">
                    {["[cười]", "[thở dài]", "[hắng giọng]"].map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        className="small-button"
                        disabled={busy || !project.subtitles.length}
                        onClick={() => insertEmotionTag(tag)}
                      >
                        {tag}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {dubbing.provider === "vieneu" && (
                <div className="video-ai-clone-note">
                  <AlertTriangle size={15} />
                  <span>
                    {l(
                      "Clone giọng chưa bật trong bản này. Khi triển khai, Dyna sẽ yêu cầu xác nhận bạn có quyền sử dụng giọng mẫu và cài runtime riêng nặng hơn.",
                      "Voice cloning is not enabled in this build. Its future implementation will require confirming rights to the reference voice and a heavier separate runtime.",
                      "此版本尚未启用声音克隆。后续实现将要求确认参考声音的使用权，并安装更大的独立运行环境。",
                    )}
                  </span>
                </div>
              )}
              <div className="video-ai-dubbing-action">
                <div>
                  <strong>
                    {project.dubbing_options?.generated_at
                      ? l(
                          `Đã tạo ${project.dubbing_options.line_count || 0} câu lồng tiếng`,
                          `Generated ${project.dubbing_options.line_count || 0} dubbed lines`,
                          `已生成 ${project.dubbing_options.line_count || 0} 条配音`,
                        )
                      : l("Chưa tạo track lồng tiếng", "No dubbing track yet", "尚未生成配音轨道")}
                  </strong>
                  <span>
                    {dubbingNeedsGeneration
                      ? l(
                          "Công cụ, giọng, phong cách hoặc tốc độ đã thay đổi; cần tạo lại trước khi xuất.",
                          "Engine, voice, style, or speed changed; regenerate before exporting.",
                          "引擎、语音、风格或速度已更改；导出前需要重新生成。",
                        )
                      : l(
                          "Bạn có thể thay đổi hai mức âm lượng mà không cần tạo lại giọng.",
                          "You can change both volume levels without regenerating speech.",
                          "无需重新生成语音即可调整两个音量。",
                        )}
                    {(project.dubbing_options?.speed_warnings?.length || 0) > 0 && (
                      <span className="video-ai-speed-summary">
                        <Gauge size={12} />
                        {l(
                          `${project.dubbing_options?.speed_warnings?.length} dòng đang bị ép tốc độ, cao nhất ${project.dubbing_options?.max_speed || 1}x.`,
                          `${project.dubbing_options?.speed_warnings?.length} lines are time-compressed, up to ${project.dubbing_options?.max_speed || 1}x.`,
                          `${project.dubbing_options?.speed_warnings?.length} 条语音被加速，最高 ${project.dubbing_options?.max_speed || 1}x。`,
                        )}
                      </span>
                    )}
                  </span>
                </div>
                <button
                  className="primary-button"
                  disabled={
                    busy
                    || action === "dubbing"
                    || !hasTranslations
                    || !selectedTtsProvider?.ready
                  }
                  onClick={() => void runDubbing()}
                >
                  {project.status === "dubbing" || action === "dubbing"
                    ? <LoaderCircle size={15} className="spin" />
                    : <AudioLines size={15} />}
                  {project.dubbing_options?.generated_at
                    ? l("Tạo lại lồng tiếng", "Regenerate dubbing", "重新生成配音")
                    : l("Tạo lồng tiếng", "Generate dubbing", "生成配音")}
                </button>
              </div>
              {dubbingPreviewUrl && (
                <audio
                  className="video-ai-dubbing-preview"
                  controls
                  preload="metadata"
                  src={dubbingPreviewUrl}
                />
              )}
              {!selectedTtsProvider?.ready && (
                <div className="video-ai-dubbing-warning">
                  <AlertTriangle size={14} />
                  {l(
                    `Thiếu ${selectedTtsProvider?.name || "công cụ giọng đọc"}; hãy cài requirements-video-ai.txt.`,
                    `${selectedTtsProvider?.name || "Speech engine"} is missing; install requirements-video-ai.txt.`,
                    `缺少 ${selectedTtsProvider?.name || "语音引擎"}；请安装 requirements-video-ai.txt。`,
                  )}
                </div>
              )}
            </div>
          </Section>

          <Section title={`6. ${l("Làm mờ và xuất video", "Blur and export", "模糊并导出视频")}`}>
            <div className="video-ai-export">
              <div className="video-ai-export-column">
                <div className="video-ai-subtitle-detector">
                  <ScanSearch size={20} />
                  <div>
                    <strong>
                      {l(
                        "Tự tìm vùng phụ đề cứng",
                        "Detect hard subtitle area",
                        "自动查找硬字幕区域",
                      )}
                    </strong>
                    <span>
                      {project.subtitle_detection?.detected
                        ? l(
                            `Tin cậy ${project.subtitle_detection.confidence}% · thấy chữ trong ${project.subtitle_detection.candidate_frames}/${project.subtitle_detection.sampled_frames} khung hình.`,
                            `${project.subtitle_detection.confidence}% confidence · text found in ${project.subtitle_detection.candidate_frames}/${project.subtitle_detection.sampled_frames} frames.`,
                            `置信度 ${project.subtitle_detection.confidence}% · 在 ${project.subtitle_detection.candidate_frames}/${project.subtitle_detection.sampled_frames} 帧中发现文字。`,
                          )
                        : project.subtitle_detection?.detected_at
                          ? l(
                              "Chưa tìm thấy vùng đủ rõ; bạn vẫn có thể chỉnh khung thủ công.",
                              "No clear region was found; you can still adjust the frame manually.",
                              "未找到足够清晰的区域；你仍可手动调整边框。",
                            )
                          : l(
                              "Dyna phân tích 24 khung hình và tự đặt khung làm mờ.",
                              "Dyna analyzes 24 frames and places the blur box automatically.",
                              "Dyna 分析 24 帧并自动放置模糊框。",
                            )}
                    </span>
                  </div>
                  <button
                    className="small-button primary"
                    disabled={busy || action === "detect" || !capabilities?.vision?.ready}
                    onClick={() => void runSubtitleDetection()}
                  >
                    {project.status === "detecting" || action === "detect"
                      ? <LoaderCircle size={14} className="spin" />
                      : <ScanSearch size={14} />}
                    {project.subtitle_detection?.detected_at
                      ? l("Tìm lại", "Detect again", "重新检测")
                      : l("Tự tìm", "Detect", "自动查找")}
                  </button>
                  {!capabilities?.vision?.ready && (
                    <small>
                      {l(
                        "Thiếu OpenCV; hãy cài requirements-video-ai.txt.",
                        "OpenCV is missing; install requirements-video-ai.txt.",
                        "缺少 OpenCV；请安装 requirements-video-ai.txt。",
                      )}
                    </small>
                  )}
                </div>
                <label className="switch-row">
                  <input
                    type="checkbox"
                    checked={blur.enabled}
                    disabled={busy}
                    onChange={(event) => setBlur((current) => ({ ...current, enabled: event.target.checked }))}
                  />
                  <span>{l("Làm mờ vùng phụ đề cũ", "Blur the old subtitle area", "模糊旧字幕区域")}</span>
                </label>
                <label className="video-ai-blur-strength">
                  <span>{l("Độ mờ", "Blur strength", "模糊强度")}</span>
                  <div>
                    <input
                      type="range"
                      min={2}
                      max={60}
                      step={1}
                      value={blur.strength}
                      disabled={busy || !blur.enabled}
                      onChange={(event) =>
                        setBlur((current) => ({ ...current, strength: Number(event.target.value) }))
                      }
                    />
                    <output>{blur.strength}</output>
                  </div>
                </label>
                <div className={`video-ai-region-help${blur.enabled ? "" : " disabled"}`}>
                  <strong>
                    {l(
                      "Chỉnh trực tiếp trên video",
                      "Adjust directly on the video",
                      "直接在视频上调整",
                    )}
                  </strong>
                  <span>
                    {l(
                      "Kéo bên trong khung vàng để di chuyển; kéo các cạnh hoặc góc để đổi kích thước.",
                      "Drag inside the yellow frame to move it; drag an edge or corner to resize.",
                      "拖动黄色框内部可移动，拖动边缘或角可调整大小。",
                    )}
                  </span>
                </div>
              </div>
              <div className="video-ai-export-column">
                <div className="video-ai-caption-position-note">
                  <Captions size={18} />
                  <span>
                    {l(
                      "Phụ đề dịch sẽ tự căn giữa trong vùng làm mờ.",
                      "Translated captions are centered inside the blur area.",
                      "译文会自动居中显示在模糊区域内。",
                    )}
                  </span>
                </div>
                <div className="video-ai-style-grid">
                  <label>
                    <span>{l("Phông chữ", "Font", "字体")}</span>
                    <select
                      value={style.font_name}
                      disabled={busy}
                      onChange={(event) => setStyle((current) => ({ ...current, font_name: event.target.value }))}
                    >
                      {!FONT_OPTIONS.includes(style.font_name) && (
                        <option value={style.font_name}>{style.font_name}</option>
                      )}
                      {FONT_OPTIONS.map((font) => <option key={font} value={font}>{font}</option>)}
                    </select>
                  </label>
                  <label>
                    <span>{l("Cỡ chữ", "Font size", "字号")}</span>
                    <input type="number" min={12} max={MAX_SUBTITLE_FONT_SIZE} value={style.font_size} disabled={busy} onChange={(event) => setStyle((current) => ({ ...current, font_size: Number(event.target.value) }))} />
                  </label>
                </div>
              </div>
              <div className="video-ai-export-action">
                <div>
                  <strong>{l("Video đầu ra", "Output video", "输出视频")}</strong>
                  <span>
                    {project.output_path
                      ? project.output_path
                      : l("Dyna sẽ lưu cạnh video nguồn.", "Dyna saves next to the source video.", "Dyna 会保存在源视频旁边。")}
                  </span>
                </div>
                {project.status === "completed" && project.output_path && (
                  <button className="secondary-button" onClick={() => void window.dyna?.revealFile(project.output_path)}>
                    <FolderOpen size={14} />
                    {l("Mở thư mục", "Show in folder", "打开文件夹")}
                  </button>
                )}
                <button
                  className="primary-button video-ai-render-button"
                  disabled={busy || !project.subtitles.length || !capabilities?.ffmpeg.ready || dubbingNeedsGeneration}
                  onClick={() => void render(project.subtitles, {
                    track: "translated",
                    blur,
                    style,
                    dubbing,
                  })}
                >
                  <WandSparkles size={16} />
                  {project.status === "completed"
                    ? l("Xuất một bản mới", "Export another version", "导出新版本")
                    : l("Xuất video", "Export video", "导出视频")}
                </button>
              </div>
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
