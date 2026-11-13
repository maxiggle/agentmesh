import { ArrowUpRight, GitHubIcon, MenuIcon } from "@/app/_components/icons";
import { Logo } from "@/app/_components/logo";
import { Container } from "@/app/_components/ui";
import { links, version } from "@/app/_lib/links";

const nav = [
  { label: "Bounties", href: "#bounties" },
  { label: "Messages", href: "#messages" },
  { label: "Tools", href: "#tools" },
  { label: "Security", href: "#security" },
  { label: "Docs", href: links.guide, external: true },
];

function NavLink({ label, href, external }: (typeof nav)[number]) {
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noreferrer" } : {})}
      className="inline-flex items-center gap-1 rounded-md px-3 py-2 text-sm text-muted transition-colors hover:text-ink"
    >
      {label}
      {external ? <ArrowUpRight className="size-3" /> : null}
    </a>
  );
}

export function Header() {
  return (
    <>
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-ink px-3 py-2 text-bg focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <a
        href={links.pypi}
        target="_blank"
        rel="noreferrer"
        className="block bg-ink py-2 text-center text-xs text-bg transition-opacity hover:opacity-90"
      >
        <span className="font-mono">ehor {version}</span> is on PyPI · bounties live on NEAR testnet{" "}
        <span aria-hidden="true">→</span>
      </a>
      <header className="sticky top-0 z-40 border-b border-line/70 bg-bg/80 backdrop-blur-md">
        <Container className="flex h-16 items-center justify-between gap-4">
          <a href="#top" className="shrink-0 text-ink" aria-label="ehor home">
            <Logo id="nav-logo" className="h-7 w-auto" />
          </a>
          <nav aria-label="Main" className="hidden items-center md:flex">
            {nav.map((item) => (
              <NavLink key={item.label} {...item} />
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <a
              href={links.repo}
              target="_blank"
              rel="noreferrer"
              aria-label="ehor on GitHub"
              className="rounded-md p-2 text-muted transition-colors hover:text-ink"
            >
              <GitHubIcon className="size-5" />
            </a>
            <a
              href="#install"
              className="hidden h-9 items-center rounded-full bg-ink px-4 text-sm font-medium text-bg transition-colors hover:bg-accent hover:text-accent-ink sm:inline-flex"
            >
              Get started
            </a>
            <details className="group relative md:hidden">
              <summary
                aria-label="Menu"
                className="flex cursor-pointer list-none rounded-md p-2 text-ink [&::-webkit-details-marker]:hidden"
              >
                <MenuIcon className="size-5" />
              </summary>
              <nav
                aria-label="Mobile"
                className="absolute right-0 mt-2 flex w-56 flex-col rounded-xl border border-line bg-surface p-2 shadow-lg"
              >
                {nav.map((item) => (
                  <NavLink key={item.label} {...item} />
                ))}
                <a
                  href="#install"
                  className="mt-1 rounded-md bg-ink px-3 py-2 text-center text-sm font-medium text-bg"
                >
                  Get started
                </a>
              </nav>
            </details>
          </div>
        </Container>
      </header>
    </>
  );
}
