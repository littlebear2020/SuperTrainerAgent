"use client";

import { useEffect, useState } from "react";

import { PERFORMANCE_THRESHOLDS } from "@/core/theme/tokens";

export interface PerformanceDegradeResult {
  /** Should reduce motion (disable motion animations + 3D tilt + glint) */
  reduceMotion: boolean;
  /** Should pause blob animations */
  pauseBlobs: boolean;
  /** Should disable backdrop-filter blur */
  disableBlur: boolean;
  /** Degradation reason */
  reason: string | null;
}

export function usePerformanceDegrade(): PerformanceDegradeResult {
  const [state, setState] = useState<PerformanceDegradeResult>({
    reduceMotion: false,
    pauseBlobs: false,
    disableBlur: false,
    reason: null,
  });

  useEffect(() => {
    // 1. prefers-reduced-motion
    const motionMQ = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (motionMQ.matches) {
      setState({
        reduceMotion: true,
        pauseBlobs: true,
        disableBlur: true,
        reason: "prefers-reduced-motion",
      });
      return;
    }

    // 2. hardwareConcurrency
    const cores = navigator.hardwareConcurrency ?? 8;
    if (cores < PERFORMANCE_THRESHOLDS.minHardwareConcurrency) {
      setState({
        reduceMotion: false,
        pauseBlobs: true,
        disableBlur: false,
        reason: "low-cores",
      });
      return;
    }

    // 3. deviceMemory
    const memory =
      (navigator as unknown as { deviceMemory?: number }).deviceMemory ?? 8;
    if (memory < PERFORMANCE_THRESHOLDS.minDeviceMemory) {
      setState({
        reduceMotion: false,
        pauseBlobs: false,
        disableBlur: true,
        reason: "low-memory",
      });
      return;
    }

    // 4. Battery API (optional, async)
    if ("getBattery" in navigator) {
      (
        navigator as unknown as {
          getBattery: () => Promise<{ level: number }>;
        }
      )
        .getBattery()
        .then((battery) => {
          if (battery.level < PERFORMANCE_THRESHOLDS.lowBattery / 100) {
            setState({
              reduceMotion: true,
              pauseBlobs: true,
              disableBlur: true,
              reason: "low-battery",
            });
          }
        })
        .catch(() => {
          /* Battery API not available, skip */
        });
    }
  }, []);

  return state;
}
