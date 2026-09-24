import type { ReactNode } from "react";

interface Props {
  /** Small mono line above the title — where the reader is. */
  eyebrow?: ReactNode;
  title: ReactNode;
  /** One sentence under the title: what this screen is for. */
  lede?: ReactNode;
  /** Right-hand slot on wide screens; wraps under the title on narrow ones. */
  actions?: ReactNode;
  /** Sits beside the title, e.g. the job's state badge. */
  aside?: ReactNode;
  mono?: boolean;
}

/** Every page opens the same way, so the reader always knows where to look
 * for "where am I" and "what can I do here". */
export function PageHeader({ eyebrow, title, lede, actions, aside, mono = false }: Props) {
  return (
    <header className="page-head">
      <div className="page-head-main">
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <div className="page-title-row">
          <h1 className={mono ? "page-title is-mono" : "page-title"}>{title}</h1>
          {aside}
        </div>
        {lede && <p className="page-lede">{lede}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
