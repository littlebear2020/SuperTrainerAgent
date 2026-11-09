/**
 * Voice production React Query hooks.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteProductionTask,
  extractDocText,
  getProductionTask,
  listProductionTasks,
  resumePptToVideo,
  submitPptToVideo,
  submitScriptToAudio,
} from "./production-api";
import type { PptToVideoParams, ScriptToAudioParams } from "./production-types";

// ── Query keys ──────────────────────────────────────────────────────────

const PRODUCTION_KEY = "voiceProduction";

// ── List tasks ──────────────────────────────────────────────────────────

export function useProductionTasks() {
  return useQuery({
    queryKey: [PRODUCTION_KEY],
    queryFn: listProductionTasks,
    refetchInterval: (query) => {
      const tasks = query.state.data;
      if (!tasks) return false;
      const hasActive = tasks.some(
        (t) => t.status !== "ready" && t.status !== "failed",
      );
      return hasActive ? 3000 : false;
    },
  });
}

// ── Single task (with polling) ──────────────────────────────────────────

// ── Submit script-to-audio ──────────────────────────────────────────────

export function useSubmitScriptToAudio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: ScriptToAudioParams) => submitScriptToAudio(params),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PRODUCTION_KEY] });
    },
  });
}

// ── Extract doc text ────────────────────────────────────────────────────

export function useExtractDocText() {
  return useMutation({
    mutationFn: (file: File) => extractDocText(file),
  });
}

// ── Delete task ─────────────────────────────────────────────────────────

export function useDeleteProductionTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => deleteProductionTask(taskId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PRODUCTION_KEY] });
    },
  });
}

// ── Submit PPT-to-video ──────────────────────────────────────────────────

export function useSubmitPptToVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: PptToVideoParams & { file: File }) => {
      const { file, ...rest } = params;
      return submitPptToVideo(file, rest);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PRODUCTION_KEY] });
    },
  });
}

// ── Resume PPT-to-video (font gate bypass) ───────────────────────────────

export function useResumePptToVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => resumePptToVideo(taskId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [PRODUCTION_KEY] });
    },
  });
}
