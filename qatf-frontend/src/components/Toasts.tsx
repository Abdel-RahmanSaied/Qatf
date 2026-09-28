import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useI18n } from "../i18n/I18nProvider";
import { Icon } from "./Icon";

interface Toast { id: number; message: string; kind: "error" | "ok" }
interface ToastApi { push: (message: string, kind?: "error" | "ok") => void }

const ToastContext = createContext<ToastApi>({ push: () => {} });

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);
  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);
  const push = useCallback((message: string, kind: "error" | "ok" = "error") => {
    const id = nextId.current++;
    setToasts((current) => [...current, { id, message, kind }]);
    window.setTimeout(() => dismiss(id), 6000);
  }, [dismiss]);
  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      {/* A polite live region that never takes focus: the stack announces
          itself without pulling the reader away from what they were doing. */}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.kind}`}>
            <span className="toast-icon" aria-hidden="true">
              <Icon name={toast.kind === "ok" ? "check" : "alert"} size={16} />
            </span>
            <span className="toast-text">{toast.message}</span>
            <button
              type="button"
              className="toast-close"
              aria-label={t.app.close}
              onClick={() => dismiss(toast.id)}
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
