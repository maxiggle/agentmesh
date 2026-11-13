import type { ReactNode } from "react";
import { ArrowUpRight } from "@/app/_components/icons";

export function Container({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mx-auto w-full max-w-[120rem] px-4 sm:px-8 lg:px-10 ${className}`}>
      {children}
    </div>
  );
}

type SectionProps = {
  id: string;
  eyebrow: string;
  title: ReactNode;
  intro?: ReactNode;
  children: ReactNode;
  className?: string;
};

export function Section({ id, eyebrow, title, intro, children, className = "" }: SectionProps) {
  const headingId = `${id}-title`;
  return (
    <section id={id} aria-labelledby={headingId} className={`py-20 sm:py-28 ${className}`}>
      <Container>
        <p className="font-mono text-xs tracking-widest text-accent uppercase">{eyebrow}</p>
        <h2
          id={headingId}
          className="mt-3 max-w-3xl text-4xl leading-[1.05] font-semibold tracking-tight text-balance sm:text-5xl"
        >
          {title}
        </h2>
        {intro ? (
          <p className="mt-5 max-w-2xl text-lg leading-relaxed text-pretty text-muted">{intro}</p>
        ) : null}
        <div className="mt-12">{children}</div>
      </Container>
    </section>
  );
}

export function Em({ children }: { children: ReactNode }) {
  return <span className="em text-accent">{children}</span>;
}

type ButtonProps = {
  href: string;
  children: ReactNode;
  variant?: "primary" | "secondary";
  external?: boolean;
};

export function Button({ href, children, variant = "primary", external = false }: ButtonProps) {
  const styles =
    variant === "primary"
      ? "bg-ink text-bg hover:bg-accent hover:text-accent-ink"
      : "border border-line bg-surface text-ink hover:border-accent";
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noreferrer" } : {})}
      className={`inline-flex h-11 items-center gap-1.5 rounded-full px-5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${styles}`}
    >
      {children}
      {external ? <ArrowUpRight className="size-3.5" /> : null}
    </a>
  );
}

export function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[0.85em] text-ink">
      {children}
    </code>
  );
}
