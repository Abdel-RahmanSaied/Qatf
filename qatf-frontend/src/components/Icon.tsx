import { HugeiconsIcon } from "@hugeicons/react";
import type { IconSvgElement } from "@hugeicons/react";
import {
  Add01Icon, Alert02Icon, ArrowDown01Icon, ArrowLeft02Icon, ArrowRight02Icon,
  ArrowUp01Icon, AudioWave01Icon, Cancel01Icon, Clock01Icon, Delete02Icon,
  Download04Icon, Edit02Icon, Film01Icon, Folder01Icon, GlobalIcon, Home01Icon,
  InformationCircleIcon, Link01Icon, PauseIcon, Scissor01Icon,
  SlidersHorizontalIcon, SparklesIcon, StopCircleIcon, SubtitleIcon, Tick02Icon,
  Upload01Icon, Video01Icon,
} from "@hugeicons/core-free-icons";

/** The app's one icon set: Hugeicons (free, MIT) — the stroke family the
 * Saudi DGA "Platforms Code" design system is drawn in. Imported per icon, so
 * only the ones listed here reach the bundle.
 *
 * Every icon is decorative by default (`aria-hidden`) — the control that
 * carries it owns the accessible name. Pass `label` only for an icon that
 * stands alone and means something. */
const ICONS = {
  plus: Add01Icon,
  home: Home01Icon,
  video: Video01Icon,
  settings: SlidersHorizontalIcon,
  arrowLeft: ArrowLeft02Icon,
  arrowRight: ArrowRight02Icon,
  arrowUp: ArrowUp01Icon,
  arrowDown: ArrowDown01Icon,
  chevron: ArrowDown01Icon,
  upload: Upload01Icon,
  link: Link01Icon,
  folder: Folder01Icon,
  check: Tick02Icon,
  x: Cancel01Icon,
  alert: Alert02Icon,
  info: InformationCircleIcon,
  film: Film01Icon,
  download: Download04Icon,
  sparkle: SparklesIcon,
  trash: Delete02Icon,
  text: SubtitleIcon,
  edit: Edit02Icon,
  clock: Clock01Icon,
  pause: PauseIcon,
  ban: StopCircleIcon,
  scissors: Scissor01Icon,
  audio: AudioWave01Icon,
  globe: GlobalIcon,
} satisfies Record<string, IconSvgElement>;

export type IconName = keyof typeof ICONS;

/** Icons that point along the reading direction, and so must mirror in RTL. */
const DIRECTIONAL = new Set<IconName>(["arrowLeft", "arrowRight"]);

interface Props {
  name: IconName;
  size?: number;
  className?: string;
  label?: string;
}

export function Icon({ name, size = 18, className, label }: Props) {
  const classes = ["icon", DIRECTIONAL.has(name) ? "icon-dir" : "", className ?? ""]
    .filter(Boolean).join(" ");
  return (
    <HugeiconsIcon
      icon={ICONS[name]}
      size={size}
      strokeWidth={1.75}
      color="currentColor"
      className={classes}
      aria-hidden={label ? undefined : true}
      role={label ? "img" : undefined}
      aria-label={label}
      focusable="false"
    />
  );
}
