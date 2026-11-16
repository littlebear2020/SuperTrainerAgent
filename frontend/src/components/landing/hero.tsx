"use client";

import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { FlickeringGrid } from "@/components/ui/flickering-grid";
import Galaxy from "@/components/ui/galaxy";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

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
        className="absolute inset-0 z-0 translate-y-8 mask-[url(/images/deer.svg)] mask-size-[180vw] mask-center mask-no-repeat md:mask-size-[130vh]"
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
