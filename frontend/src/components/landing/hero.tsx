"use client";

import { BarChart3, ChevronRightIcon, Palette, Search } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { FlickeringGrid } from "@/components/ui/flickering-grid";
import Galaxy from "@/components/ui/galaxy";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

const highlightIcons = [Search, BarChart3, Palette];

export function Hero({ className }: { className?: string }) {
  const { t } = useI18n();
  return (
    <div
      className={cn(
        "flex size-full flex-col items-center justify-center",
        className,
      )}
    >
      <div className="absolute inset-0 z-0 bg-black/40">
        <Galaxy
          mouseRepulsion={false}
          starSpeed={0.2}
          density={0.6}
          glowIntensity={0.35}
          twinkleIntensity={0.3}
          speed={0.5}
        />
      </div>
      <FlickeringGrid
        className="absolute inset-0 z-0 translate-y-8 mask-[url(/images/deer.svg)] mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
        squareSize={4}
        gridGap={4}
        color={"white"}
        maxOpacity={0.3}
        flickerChance={0.25}
      />
      <div className="container-md relative z-10 mx-auto flex h-screen flex-col items-center justify-center">
        <h1
          className="text-5xl font-bold md:text-7xl"
          style={{
            background: "linear-gradient(135deg, #6366f1, #ec4899)",
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
          }}
        >
          Trainer AI
        </h1>
        <p className="text-muted-foreground mt-6 text-center text-xl md:text-2xl">
          {t.home.tagline}
        </p>

        <div className="mt-10 flex flex-wrap items-center justify-center gap-4 md:gap-6">
          {t.home.highlights.map((item, i) => {
            const Icon = highlightIcons[i] ?? Search;
            return (
              <div
                key={item.label}
                className="flex flex-col items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-5 py-4 backdrop-blur-sm"
              >
                <Icon className="size-6 text-white/80" />
                <span className="text-sm font-medium">{item.label}</span>
                <span className="text-muted-foreground max-w-[160px] text-center text-xs">
                  {item.description}
                </span>
              </div>
            );
          })}
        </div>

        <Link href="/workspace" className="mt-10">
          <Button className="size-lg scale-108" size="lg">
            <span className="text-md">{t.home.enterWorkspace}</span>
            <ChevronRightIcon className="size-4" />
          </Button>
        </Link>
      </div>
    </div>
  );
}
