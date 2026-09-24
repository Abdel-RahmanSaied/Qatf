import { useCallback, useState } from "react";
import { health } from "../api/client";
import { usePolling } from "../api/poll";
import type { Health } from "../api/types";
import { useI18n } from "../i18n/I18nProvider";
import { Icon } from "./Icon";

/** Everything worth knowing BEFORE submitting an hour of audio. */
export function HealthBanner() {
  const { t } = useI18n();
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
    return (
      <div className="banner banner-error" role="alert">
        <Icon name="alert" />
        <span>{t.server.unreachable}</span>
      </div>
    );
  }
  if (!info) return null;

  const warnings: string[] = [];
  if (!info.ffmpeg) warnings.push(t.server.ffmpeg);
  if (!info.llm_ready) warnings.push(t.server.llm(info.llm_provider));
  if (info.transcribe_device === "cpu") warnings.push(t.server.cpu);
  if (!info.caption_pill_ready) warnings.push(t.server.pill);
  if (warnings.length === 0) return null;
  return (
    <div className={`banner ${info.status === "degraded" ? "banner-error" : "banner-warn"}`}>
      <Icon name="alert" />
      <ul className="banner-list">
        {warnings.map((w) => <li key={w}>{w}</li>)}
      </ul>
    </div>
  );
}
