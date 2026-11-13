import type { ReactNode } from "react";

type DiagramCardProps = {
  label: string;
  title: string;
  caption: string;
  playing: boolean;
  onToggle: () => void;
  children: ReactNode;
};

export function DiagramCard({
  label,
  title,
  caption,
  playing,
  onToggle,
  children,
}: DiagramCardProps) {
  return (
    <figure className="flex flex-col overflow-hidden rounded-2xl border border-line bg-surface">
      <p className="px-6 pt-6 text-sm text-muted sm:px-8 sm:pt-7">{label}</p>
      <div className="flex flex-1 items-center px-2 py-2 sm:px-4">{children}</div>
      <figcaption className="flex min-h-24 items-center justify-between gap-4 border-t border-line px-6 py-5 sm:px-8">
        <div aria-live="polite">
          <p className="font-medium">{title}</p>
          <p className="mt-1 text-sm text-muted">{caption}</p>
        </div>
        <button
          type="button"
          onClick={onToggle}
          className="shrink-0 rounded-lg border border-line px-3 py-2 text-sm text-muted transition-colors hover:border-ink hover:text-ink"
        >
          {playing ? "Pause" : "Play"}
        </button>
      </figcaption>
    </figure>
  );
}
