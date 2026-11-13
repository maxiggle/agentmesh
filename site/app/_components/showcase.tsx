import type { ReactNode } from "react";
import { Container } from "@/app/_components/ui";

export type ShowcasePoint = { title: string; body: ReactNode };

type ShowcaseProps = {
  id: string;
  eyebrow: string;
  title: string;
  accent: string;
  figure: ReactNode;
  points: ShowcasePoint[];
  className?: string;
};

function CheckIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      className="mt-1.5 size-5 shrink-0 text-accent"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="2.5" y="2.5" width="15" height="15" rx="3" />
      <path d="m6.5 10.2 2.4 2.4 4.6-4.9" />
    </svg>
  );
}

export function Showcase({
  id,
  eyebrow,
  title,
  accent,
  figure,
  points,
  className = "",
}: ShowcaseProps) {
  const headingId = `${id}-title`;
  return (
    <section id={id} aria-labelledby={headingId} className={`py-20 sm:py-28 ${className}`}>
      <Container>
        <p className="font-mono text-xs tracking-widest text-accent uppercase">{eyebrow}</p>
        <h2
          id={headingId}
          className="mt-3 text-4xl leading-[1.05] font-semibold tracking-tight text-balance sm:text-6xl"
        >
          {title} <br />
          <span className="text-accent">{accent}</span>
        </h2>
        <div className="mt-14 grid items-center gap-12 lg:grid-cols-[minmax(0,52rem)_minmax(0,40rem)] lg:gap-16 xl:gap-24">
          {figure}
          <ul className="divide-y divide-line">
            {points.map((point) => (
              <li key={point.title} className="flex gap-4 py-8 first:pt-0 last:pb-0">
                <CheckIcon />
                <div>
                  <h3 className="text-2xl font-medium tracking-tight">{point.title}</h3>
                  <p className="mt-3 leading-relaxed text-muted">{point.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </Container>
    </section>
  );
}
