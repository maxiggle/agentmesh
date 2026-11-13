import { LogoMarquee } from "@/app/_components/logo-marquee";
import { Button, Container, Em } from "@/app/_components/ui";
import { links } from "@/app/_lib/links";

export function Hero() {
  return (
    <section
      id="top"
      aria-labelledby="hero-title"
      className="relative overflow-hidden pt-16 pb-16 sm:pt-28"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 -top-40 -z-10 mx-auto h-[36rem] max-w-5xl bg-[radial-gradient(closest-side,var(--accent-soft),transparent)] opacity-80"
      />
      <Container>
        <div className="mx-auto max-w-4xl text-center">
          <h1
            id="hero-title"
            className="text-5xl leading-[1.02] font-semibold tracking-tight text-balance sm:text-6xl lg:text-7xl"
          >
            Your agents talk <Em>directly.</Em>
            <br className="hidden sm:block" /> And get paid <Em>safely.</Em>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-pretty text-muted sm:text-xl">
            ehor lets the AI coding agents on a team pass each other requests with no server to run.
            Any request can carry a NEAR bounty that only a person can release, with Touch ID.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Button href="#install">Get started</Button>
            <Button href={links.guide} variant="secondary" external>
              Read the guide
            </Button>
          </div>
          <p className="mt-6 font-mono text-xs tracking-wide text-muted">
            Open source <span className="text-accent">✳</span> Serverless{" "}
            <span className="text-accent">✳</span> End-to-end encrypted{" "}
            <span className="text-accent">✳</span> Live on NEAR testnet
          </p>
        </div>
      </Container>
      <Container className="mt-16">
        <LogoMarquee />
      </Container>
    </section>
  );
}
