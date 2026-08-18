import { useCallback, useState } from "react";
import { health } from "../api/client";
import { usePolling } from "../api/poll";
import type { Health } from "../api/types";

/** Everything worth knowing BEFORE submitting an hour of audio. */
export function HealthBanner() {
  const [info, setInfo] = useState<Health | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setInfo(await health());
      setUnreachable(false);
    } catch {
      setUnreachable(true);
    }
  }, []);
  usePolling(refresh, 30_000);

  if (unreachable) {
    return <div className="banner banner-error">Cannot reach the qatf server — retrying.</div>;
  }
  if (!info) return null;

  const warnings: string[] = [];
  if (!info.ffmpeg) warnings.push("ffmpeg is missing on the server — nothing can render.");
  if (!info.llm_ready) {
    warnings.push(
      `Stage 3 provider "${info.llm_provider}" has no credential — ` +
      "jobs will fail after transcription has already run.");
  }
  if (info.transcribe_device === "cpu") {
    warnings.push("Transcription will run on CPU — large-v3 on an hour of audio is slow. " +
      "Consider whisper=small while iterating.");
  }
  if (warnings.length === 0) return null;
  return (
    <div className={`banner ${info.status === "degraded" ? "banner-error" : "banner-warn"}`}>
      {warnings.map((w) => <div key={w}>{w}</div>)}
    </div>
  );
}
