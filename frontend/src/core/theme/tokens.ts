/** Floating color blobs — Light & Dark configurations */
export const BLOBS = {
  light: {
    rootBg: "#f1f3f9",
    items: [
      {
        position: "-top-[150px] -right-[100px]",
        size: 700,
        gradient: "radial-gradient(circle, #3b82f6 0%, #1d4ed8 100%)",
        duration: 22,
        delay: 0,
        blur: 110,
        opacity: 0.15,
        animate: {
          x: [0, 110, -75, 0],
          y: [0, -85, 95, 0],
          scale: [1, 1.2, 0.85, 1],
          rotate: [0, 120, 240, 360],
          borderRadius: [
            "42% 58% 70% 30% / 45% 45% 55% 55%",
            "70% 30% 52% 48% / 60% 40% 60% 40%",
            "40% 60% 30% 70% / 40% 50% 50% 60%",
            "42% 58% 70% 30% / 45% 45% 55% 55%",
          ],
        },
      },
      {
        position: "-bottom-[120px] -left-[80px]",
        size: 600,
        gradient: "radial-gradient(circle, #c084fc 0%, #8b5cf6 100%)",
        duration: 19,
        delay: 2,
        blur: 105,
        opacity: 0.15,
        animate: {
          x: [0, -95, 110, 0],
          y: [0, 95, -75, 0],
          scale: [1, 0.85, 1.25, 1],
          rotate: [360, 240, 120, 0],
          borderRadius: [
            "50% 50% 30% 70% / 50% 60% 40% 60%",
            "42% 58% 70% 30% / 45% 45% 55% 55%",
            "70% 30% 52% 48% / 60% 40% 60% 40%",
            "50% 50% 30% 70% / 50% 60% 40% 60%",
          ],
        },
      },
      {
        position: "top-[35%] left-[10%]",
        size: 520,
        gradient: "radial-gradient(circle, #06b6d4 0%, #0891b2 100%)",
        duration: 21,
        delay: 4,
        blur: 115,
        opacity: 0.15,
        animate: {
          x: [0, 85, -95, 0],
          y: [0, 95, -85, 0],
          scale: [1, 1.2, 0.85, 1],
          rotate: [0, 90, 180, 270, 360],
          borderRadius: [
            "40% 60% 70% 30% / 40% 50% 50% 60%",
            "70% 30% 52% 48% / 60% 40% 60% 40%",
            "50% 50% 30% 70% / 50% 60% 40% 60%",
            "40% 60% 70% 30% / 40% 50% 50% 60%",
          ],
        },
      },
      {
        position: "bottom-[20%] right-[15%]",
        size: 450,
        gradient: "radial-gradient(circle, #f472b6 0%, #ec4899 100%)",
        duration: 23,
        delay: 6,
        blur: 108,
        opacity: 0.15,
        animate: {
          x: [0, -75, 85, 0],
          y: [0, -85, 75, 0],
          scale: [1, 0.85, 1.15, 1],
          rotate: [360, 180, 0],
          borderRadius: [
            "42% 58% 70% 30% / 45% 45% 55% 55%",
            "50% 50% 30% 70% / 50% 60% 40% 60%",
            "70% 30% 52% 48% / 60% 40% 60% 40%",
            "42% 58% 70% 30% / 45% 45% 55% 55%",
          ],
        },
      },
    ],
  },
  dark: {
    rootBg: "#040810",
    items: [
      {
        position: "-top-[120px] -right-[50px]",
        size: 650,
        gradient: "radial-gradient(circle, #00f2fe 0%, #0b4f8d 100%)",
        duration: 24,
        delay: 0,
        blur: 88,
        opacity: 0.22,
        animate: {
          x: [0, 110, -75, 0],
          y: [0, -85, 95, 0],
          scale: [1, 1.25, 0.9, 1],
          rotate: [0, 120, 240, 360],
          borderRadius: [
            "42% 58% 70% 30% / 45% 45% 55% 55%",
            "70% 30% 52% 48% / 60% 40% 60% 40%",
            "40% 60% 30% 70% / 40% 50% 50% 60%",
            "42% 58% 70% 30% / 45% 45% 55% 55%",
          ],
        },
      },
      {
        position: "-bottom-[100px] -left-[60px]",
        size: 580,
        gradient: "radial-gradient(circle, #7c3aed 0%, #2e0854 100%)",
        duration: 22,
        delay: 2,
        blur: 85,
        opacity: 0.2,
        animate: {
          x: [0, -95, 110, 0],
          y: [0, 85, -70, 0],
          scale: [1, 0.9, 1.2, 1],
          rotate: [360, 240, 120, 0],
          borderRadius: [
            "50% 50% 30% 70% / 50% 60% 40% 60%",
            "42% 58% 70% 30% / 45% 45% 55% 55%",
            "70% 30% 52% 48% / 60% 40% 60% 40%",
            "50% 50% 30% 70% / 50% 60% 40% 60%",
          ],
        },
      },
      {
        position: "top-[35%] left-[8%]",
        size: 520,
        gradient: "radial-gradient(circle, #10b981 0%, #043e2b 100%)",
        duration: 25,
        delay: 4,
        blur: 90,
        opacity: 0.16,
        animate: {
          x: [0, 85, -95, 0],
          y: [0, 85, -75, 0],
          scale: [1, 1.2, 0.85, 1],
          rotate: [0, 90, 180, 270, 360],
          borderRadius: [
            "40% 60% 70% 30% / 40% 50% 50% 60%",
            "70% 30% 52% 48% / 60% 40% 60% 40%",
            "50% 50% 30% 70% / 50% 60% 40% 60%",
            "40% 60% 70% 30% / 40% 50% 50% 60%",
          ],
        },
      },
      {
        position: "bottom-[20%] right-[10%]",
        size: 480,
        gradient: "radial-gradient(circle, #ec4899 0%, #580c30 100%)",
        duration: 23,
        delay: 6,
        blur: 87,
        opacity: 0.15,
        animate: {
          x: [0, -75, 85, 0],
          y: [0, -85, 75, 0],
          scale: [1, 0.9, 1.15, 1],
          rotate: [360, 180, 0],
          borderRadius: [
            "42% 58% 70% 30% / 45% 45% 55% 55%",
            "50% 50% 30% 70% / 50% 60% 40% 60%",
            "70% 30% 52% 48% / 60% 40% 60% 40%",
            "42% 58% 70% 30% / 45% 45% 55% 55%",
          ],
        },
      },
    ],
  },
};

/** Dot grid texture overlay */
export const DOT_GRID = {
  size: 32,
  color: "rgba(99, 102, 241, 0.03)",
} as const;

/** Performance degradation thresholds */
export const PERFORMANCE_THRESHOLDS = {
  minHardwareConcurrency: 4,
  minDeviceMemory: 4,
  lowBattery: 20,
} as const;
