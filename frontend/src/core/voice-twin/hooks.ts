/**
 * Voice Twin React Query hooks.
 *
 * Follows the budget/hooks.ts pattern: useQuery for reads,
 * useMutation for writes, queryClient.invalidateQueries on success.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  deleteVoiceTwin,
  getVoiceTwinStatus,
  listModels,
  listPresetVoices,
  listVoiceTwins,
  prescreenAudio,
  synthesizeAudio,
  uploadVoiceTwin,
} from "./api";
import type { SynthesizeParams, UploadVoiceTwinParams } from "./types";
import {
  createVoicePreviewController,
  type VoicePreviewController,
} from "./voice-preview-controller";

// ── Query keys ──────────────────────────────────────────────────────────

const VOICE_TWIN_KEY = "voiceTwins";

// ── Models ──────────────────────────────────────────────────────────────

// 2026-10-06 合并批改名：与 core/models 的 useModels 同名双实现，误导入风险
export function useVoiceTwinModels() {
  return useQuery({
    queryKey: [VOICE_TWIN_KEY, "models"],
    queryFn: listModels,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });
}

// ── Voice Twins list ────────────────────────────────────────────────────

export function useVoiceTwins() {
  return useQuery({
    queryKey: [VOICE_TWIN_KEY],
    queryFn: listVoiceTwins,
  });
}

// ── Preset voices ────────────────────────────────────────────────────────

export function usePresetVoices() {
  return useQuery({
    queryKey: [VOICE_TWIN_KEY, "presetVoices"],
    queryFn: listPresetVoices,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });
}

// ── Prescreen ───────────────────────────────────────────────────────────

export function usePrescreenAudio() {
  return useMutation({
    mutationFn: (file: File) => prescreenAudio(file),
  });
}

// ── Upload ──────────────────────────────────────────────────────────────

export function useUploadVoiceTwin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: UploadVoiceTwinParams) => uploadVoiceTwin(params),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VOICE_TWIN_KEY] });
    },
  });
}

// ── Training status ─────────────────────────────────────────────────────

export function useVoiceTwinStatus(voiceId: string | undefined) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: [VOICE_TWIN_KEY, "status", voiceId],
    queryFn: () => getVoiceTwinStatus(voiceId!),
    enabled: !!voiceId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "ready" || status === "failed") return false;
      return 5000;
    },
  });

  // On transition into a terminal status, refresh the list so VoiceCard
  // badges/player (which read list data, not this poll) leave "training".
  // Tracking prev status ensures this fires once per transition, not per poll.
  const status = query.data?.status;
  const prevStatusRef = useRef(status);
  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;
    if (status !== prev && (status === "ready" || status === "failed")) {
      void qc.invalidateQueries({ queryKey: [VOICE_TWIN_KEY] });
    }
  }, [status, qc]);

  return query;
}

// ── Synthesize ──────────────────────────────────────────────────────────

export function useSynthesize() {
  return useMutation({
    mutationFn: ({
      voiceId,
      params,
    }: {
      voiceId: string;
      params: SynthesizeParams;
    }) => synthesizeAudio(voiceId, params),
  });
}

// ── Delete ──────────────────────────────────────────────────────────────

export function useDeleteVoiceTwin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (voiceId: string) => deleteVoiceTwin(voiceId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [VOICE_TWIN_KEY] });
    },
  });
}

/**
 * React wrapper around VoicePreviewController. `toggle`/`stop` are stable
 * (the controller owns mutable state), so consumers can memoize freely.
 * Re-renders are driven by the controller's onChange callback.
 */
export function useVoicePreview() {
  const ctrlRef = useRef<VoicePreviewController | null>(null);
  const [, setBump] = useState(0);

  ctrlRef.current ??= createVoicePreviewController({
    onChange: () => setBump((n) => n + 1),
  });
  const ctrl = ctrlRef.current;

  useEffect(() => {
    return () => ctrl.dispose();
  }, [ctrl]);

  return {
    playingId: ctrl.getPlayingId(),
    loadingId: ctrl.getLoadingId(),
    toggle: ctrl.toggle,
    stop: ctrl.dispose,
  };
}
