import { Header } from "@/components/landing/header";
import { Hero } from "@/components/landing/hero";

export default function LandingPage() {
  return (
    <div className="min-h-screen w-full bg-[#080b14]">
      <Header />
      <main className="flex w-full flex-col">
        <Hero />
      </main>
    </div>
  );
}
