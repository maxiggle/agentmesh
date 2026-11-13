import { Header } from "@/app/_components/header";
import { Security } from "@/app/_sections/escrow";
import { Platforms, Tools } from "@/app/_sections/features";
import { Footer } from "@/app/_sections/footer";
import { Hero } from "@/app/_sections/hero";
import { ClosingCta, Install } from "@/app/_sections/install";
import { Bounties, Messages } from "@/app/_sections/showcases";

export default function Home() {
  return (
    <>
      <Header />
      <main id="main">
        <Hero />
        <Bounties />
        <Messages />
        <Tools />
        <Security />
        <Platforms />
        <Install />
        <ClosingCta />
      </main>
      <Footer />
    </>
  );
}
