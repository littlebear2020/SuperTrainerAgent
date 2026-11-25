import type { AgentThread } from "@/core/threads/types";
import type { VideoTaskBrief } from "@/core/video-gen/types";
import type { WorkCapability } from "@/core/workspace/video-assets-route";

export type WorkMediaKind = "video" | "image" | "audio" | "document" | "other";
export type AgentDeliveryState =
  | "delivered"
  | "not_delivered"
  | "partial"
  | "missing"
  | "unknown";

export interface ArtifactFileSummary {
  path: string;
  name: string;
  media_kind: WorkMediaKind;
  size: number | null;
  modified_at: string | null;
  exists: boolean;
}

export interface ThreadArtifactSummary {
  thread_id: string;
  delivery_state: AgentDeliveryState;
  files: ArtifactFileSummary[];
}

export type WorkSource =
  | { kind: "agent"; thread: AgentThread }
  | { kind: "video_task"; task: VideoTaskBrief };

export interface WorkFile {
  id: string;
  name: string | null;
  mediaKind: WorkMediaKind;
  size: number | null;
  modifiedAt: string | null;
  exists: boolean;
  previewUrl: string | null;
  downloadUrl: string | null;
}

export interface WorkSummary {
  id: string;
  capability: WorkCapability;
  title: string;
  source: WorkSource;
  agentDeliveryState: AgentDeliveryState | null;
  sortAt: string | null;
  files: WorkFile[];
  primaryFile: WorkFile | null;
}
