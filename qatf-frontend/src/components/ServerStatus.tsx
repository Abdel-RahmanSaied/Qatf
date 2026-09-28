import { useCallback, useState } from "react";
import { health } from "../api/client";
import { usePolling } from "../api/poll";
import type { Health } from "../api/types";
import { useI18n } from "../i18n/I18nProvider";

/** A standing answer to "is the server there, and what will it run on?" in the
 * shell, so it is visible from every page rather than only on the two that
 * carry the full HealthBanner. It summarises; the banner still explains. */
export function ServerStatus() {
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

  const tone =
    unreachable ? "is-down" :
    info === null ? "is-unknown" :
    info.status === "degraded" || !info.ffmpeg || !info.llm_ready ? "is-warn" :
    "is-ok";

  const label =
    tone === "is-down" ? t.server.down :
    tone === "is-unknown" ? t.server.checking :
    tone === "is-warn" ? t.server.problems :
    t.server.ready;

  return (
    <div className={`server-status ${tone}`} role="status" aria-live="polite">
      <span className="server-status-dot" aria-hidden="true" />
      <span className="server-status-text">
        <span className="server-status-label">{label}</span>
        {info && !unreachable && (
          <span className="server-status-meta mono">
            {info.transcribe_device} · {info.llm_provider}
          </span>
        )}
      </span>
    </div>
  );
}
