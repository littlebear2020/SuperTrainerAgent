"use client";

import { motion } from "motion/react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { BLOBS, DOT_GRID } from "@/core/theme/tokens";
import { usePerformanceDegrade } from "@/hooks/use-performance-degrade";

export function FloatingBlobsBackground({
  forceDark,
}: { forceDark?: boolean } = {}) {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const isDark = forceDark ?? (mounted ? resolvedTheme === "dark" : false);
  const { pauseBlobs, disableBlur, reduceMotion } = usePerformanceDegrade();
  const blobConfig = isDark ? BLOBS.dark : BLOBS.light;

  return (
    <>
      {/* Root background — CSS variable so next-themes applies it before React hydrates */}
      <div
        className="pointer-events-none fixed inset-0 -z-10"
        style={{ backgroundColor: "var(--blob-root-bg)" }}
        aria-hidden="true"
      />

      {/* Floating color blobs — defer until theme is resolved to avoid light→dark flash */}
      {mounted &&
        blobConfig.items.map((blob, idx) => (
          <motion.div
            key={`blob-${idx}`}
            className={`pointer-events-none fixed -z-10 ${blob.position}`}
            style={{
              width: blob.size,
              height: blob.size,
              background: blob.gradient,
              filter: disableBlur ? "none" : `blur(${blob.blur}px)`,
              opacity: blob.opacity,
              borderRadius: blob.animate.borderRadius[0],
              willChange: "transform",
            }}
            initial={false}
            animate={reduceMotion || pauseBlobs ? {} : blob.animate}
            transition={
              reduceMotion || pauseBlobs
                ? { duration: 0 }
                : {
                    duration: blob.duration,
                    delay: blob.delay,
                    repeat: Infinity,
                    ease: "easeInOut",
                  }
            }
          />
        ))}

      {/* Dot grid texture overlay */}
      {!reduceMotion && mounted && (
        <div
          className="pointer-events-none fixed inset-0 -z-10"
          style={{
            background: `radial-gradient(circle at center, ${DOT_GRID.color} 1px, transparent 1px)`,
            backgroundSize: `${DOT_GRID.size}px ${DOT_GRID.size}px`,
          }}
          aria-hidden="true"
        />
      )}
    </>
  );
}
