export type VideoAiJobStatus =
  | "ready"
  | "detecting"
  | "transcribing"
  | "translating"
  | "dubbing"
  | "rendering"
  | "completed"
  | "cancelled"
  | "failed";

export type VideoAiSubtitle = {
  id: string;
  start: number;
  end: number;
  text: string;
  translated_text: string;
};

export type VideoAiBlur = {
  enabled: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
  strength: number;
};

export type VideoAiStyle = {
  font_name: string;
  font_size: number;
  margin_v: number;
  outline: number;
  primary_color: string;
};

export type VideoAiDubbing = {
  enabled: boolean;
  provider: "edge" | "vieneu";
  voice: string;
  style: "tu_nhien" | "tin_tuc" | "doc_truyen";
  rate: number;
  volume: number;
  original_volume: number;
  audio_path?: string;
  generated_at?: string;
  line_count?: number;
  max_speed?: number;
  speed_warnings?: Array<{
    id: string;
    speed: number;
    slot_duration: number;
    speech_duration: number;
  }>;
};

export type VideoAiTtsStatus = {
  provider: "edge" | "vieneu";
  state: "not_installed" | "not_downloaded" | "downloaded" | "loading" | "ready" | "failed";
  ready: boolean;
  progress: number;
  stage: string;
  error: string;
  cache_path: string;
  size_bytes: number;
  expected_size_bytes: number;
};

export type VideoAiProject = {
  id: string;
  source_path: string;
  output_path: string;
  subtitle_path?: string;
  media: {
    duration_seconds: number;
    width: number;
    height: number;
    has_audio: boolean;
    video_codec: string;
    audio_codec: string;
    file_size: number;
  };
  status: VideoAiJobStatus;
  stage: string;
  progress: number;
  error: string;
  source_language: string;
  detected_language: string;
  target_language: string;
  model_name: string;
  subtitles: VideoAiSubtitle[];
  subtitle_detection?: {
    detected: boolean;
    confidence: number;
    sampled_frames: number;
    candidate_frames: number;
    region: VideoAiBlur | null;
    detected_at: string;
    requested_samples?: number;
  };
  dubbing_options?: VideoAiDubbing;
  render_options: {
    track: "source" | "translated";
    blur: VideoAiBlur;
    style: VideoAiStyle;
  };
  created_at: string;
  updated_at: string;
};

export type VideoAiCapabilities = {
  ffmpeg: { ready: boolean; path?: string };
  ffprobe: { ready: boolean; path?: string };
  asr: {
    ready: boolean;
    engine: string;
    cuda_devices: number;
    cuda_ready: boolean;
    device: "cpu" | "cuda";
    cuda_missing: string[];
    models: string[];
    install_command: string;
  };
  translation: { ready: boolean; provider: string };
  vision: {
    ready: boolean;
    engine: string;
    install_command: string;
  };
  tts: {
    ready: boolean;
    engine: string;
    providers: Array<{
      id: "edge" | "vieneu";
      name: string;
      ready: boolean;
      offline: boolean;
      voices: Array<{
        id: string;
        locale: string;
        gender: "female" | "male";
        name: string;
        region?: string;
        default_style?: "tu_nhien" | "tin_tuc" | "doc_truyen";
      }>;
      styles: Array<{
        id: "tu_nhien" | "tin_tuc" | "doc_truyen";
        name: string;
      }>;
    }>;
    voices: Array<{
      id: string;
      locale: string;
      gender: "female" | "male";
      name: string;
    }>;
    install_command: string;
  };
};

export type VideoAiRenderRequest = {
  track: "source" | "translated";
  blur: VideoAiBlur;
  style: VideoAiStyle;
  dubbing: VideoAiDubbing;
  output_path?: string;
};

export type VideoAiEditorSaveRequest = {
  subtitles: VideoAiSubtitle[];
  blur: VideoAiBlur;
  style: VideoAiStyle;
};
