// Hand-written mirror of qatf-backend/qatf/api/schemas.py. If a field changes
// there, it changes here — grep for the field name in both places.

export type JobState =
  | "queued" | "fetching" | "extracting" | "transcribing" | "selecting"
  | "planned" | "rendering" | "done" | "failed" | "cancelled";

export const TERMINAL_STATES: ReadonlySet<JobState> =
  new Set<JobState>(["done", "failed", "cancelled"]);

export const JOB_STATES: readonly JobState[] = [
  "queued", "fetching", "extracting", "transcribing", "selecting",
  "planned", "rendering", "done", "failed", "cancelled",
];

export interface JobOptions {
  clips: number;
  min_len: number;
  max_len: number;
  reframe: "crop" | "blur" | "track";
  track_tier: "fast" | "balanced" | "best";
  codec: "h264" | "h265";
  preset: string;
  resolution: string;
  ten_bit: boolean;
  crf: number;
  whisper: string;
  device: "auto" | "cuda" | "cpu";
  language: string | null;
  denoise: boolean;
  fixups: Record<string, string> | null;
  hotwords: string | null;
  initial_prompt: string | null;
  font: string;
  captions: boolean;
  per_line: number;
  transcript_source: "auto" | "captions" | "whisper";
  auto_render: boolean;
}

// Server defaults from schemas.py, EXCEPT auto_render: the server defaults to
// true; the UI defaults to false because reviewing the plan is its whole point.
export const DEFAULT_OPTIONS: JobOptions = {
  clips: 5,
  min_len: 30,
  max_len: 75,
  reframe: "crop",
  track_tier: "balanced",
  codec: "h265",
  preset: "medium",
  resolution: "1080p",
  ten_bit: false,
  crf: 20,
  whisper: "large-v3",
  device: "auto",
  language: null,
  denoise: false,
  fixups: null,
  hotwords: null,
  initial_prompt: null,
  font: "Arial",
  captions: true,
  per_line: 4,
  transcript_source: "auto",
  auto_render: false,
};

// asr.MODEL_SIZES, ordered for a dropdown (most-used first).
export const WHISPER_MODELS: readonly string[] = [
  "large-v3", "large-v3-turbo", "turbo", "large-v2", "large-v1", "large",
  "medium", "medium.en", "small", "small.en", "base", "base.en",
  "tiny", "tiny.en",
  "distil-large-v3", "distil-large-v2", "distil-medium.en", "distil-small.en",
];

// encode.PRESETS, slowest first.
export const PRESETS: readonly string[] = [
  "veryslow", "slower", "slow", "medium", "fast", "faster",
  "veryfast", "superfast", "ultrafast",
];

export interface ClipModel {
  start: number;
  end: number;
  title: string;
  hook: string;
  why: string;
  score: number;
}

export interface WordModel {
  text: string;
  start: number;
  end: number;
}

export interface TranscriptResponse {
  language: string | null;
  language_probability: number | null;
  timing_source: "asr" | "captions";
  word_count: number;
  edits_applied: number;
  edits_stale: number;
  words: WordModel[];
}

export interface ClipOutput {
  name: string;
  size_bytes: number;
  url: string;
}

export interface JobResponse {
  id: string;
  state: JobState;
  message: string;
  error: string | null;
  video: string;
  source: "upload" | "path" | "youtube";
  url: string;
  options: JobOptions;
  created_at: string;
  updated_at: string;
  language: string | null;
  device: string | null;
  word_count: number;
  transcript_cached: boolean;
  clips: ClipModel[];
  outputs: ClipOutput[];
}

export interface ProviderInfo {
  key: string;
  default_model: string;
  base_url: string | null;
  key_env: string | null;
  structured_output: "json_schema" | "json_object" | "prompt_only";
  context_tokens: number | null;
  note: string;
}

export interface Health {
  status: "ok" | "degraded";
  version: string;
  model: string;
  ffmpeg: boolean;
  media_root: string;
  max_workers: number;
  llm_provider: string;
  llm_ready: boolean;
  llm_error: string | null;
  providers: ProviderInfo[];
  cuda_devices: number;
  transcribe_device: string;
}
