import { useEffect, useRef } from "react";

/** Poll `fn` every `intervalMs`; pass null to pause. Uses setTimeout chaining,
 * not setInterval, so a slow response never overlaps the next tick. */
export function usePolling(fn: () => Promise<void>, intervalMs: number | null): void {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (intervalMs === null) return;
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        await fnRef.current();
      } catch {
        // pages track their own error state; a failed poll must not kill the loop
      }
      if (alive) timer = window.setTimeout(tick, intervalMs);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [intervalMs]);
}
