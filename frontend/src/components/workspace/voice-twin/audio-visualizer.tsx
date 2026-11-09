"use client";

import { useEffect, useRef } from "react";

interface AudioVisualizerProps {
  analyserNode: AnalyserNode | null;
  isActive: boolean;
  className?: string;
}

export function AudioVisualizer({
  analyserNode,
  isActive,
  className,
}: AudioVisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animFrameRef = useRef<number>(0);

  useEffect(() => {
    if (!analyserNode || !isActive || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // HiDPI scaling
    const dpr = window.devicePixelRatio || 1;
    const displayWidth = canvas.clientWidth;
    const displayHeight = canvas.clientHeight;
    canvas.width = displayWidth * dpr;
    canvas.height = displayHeight * dpr;
    ctx.scale(dpr, dpr);

    const bufferLength = analyserNode.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const draw = () => {
      animFrameRef.current = requestAnimationFrame(draw);
      analyserNode.getByteFrequencyData(dataArray);

      ctx.clearRect(0, 0, displayWidth, displayHeight);

      const barCount = 40;
      const gap = 2;
      const barWidth = (displayWidth - gap * (barCount - 1)) / barCount;
      const step = Math.floor(bufferLength / barCount);

      const gradient = ctx.createLinearGradient(0, 0, displayWidth, 0);
      gradient.addColorStop(0, "#818cf8");
      gradient.addColorStop(0.4, "#a78bfa");
      gradient.addColorStop(0.7, "#c084fc");
      gradient.addColorStop(1, "#22d3ee");

      for (let i = 0; i < barCount; i++) {
        const sample = dataArray[i * step] ?? 0;
        const value = sample / 255;
        const barHeight = value * displayHeight * 0.85;
        const x = i * (barWidth + gap);
        const y = (displayHeight - barHeight) / 2;

        ctx.globalAlpha = 0.3 + value * 0.7;
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.roundRect(x, y, barWidth, barHeight, 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };

    draw();
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [analyserNode, isActive]);

  if (!isActive) return null;

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{ width: "100%", height: 48 }}
    />
  );
}
