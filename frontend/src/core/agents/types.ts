export interface Agent {
  name: string;
  description: string;
  model: string | null;
  tool_groups: string[] | null;
  skills: string[] | null;
  soul?: string | null;
  /** 新会话自然语言建议 chip（seed-authored，B.3）：点按预填整句 prompt（非 /skill 插入） */
  suggestion_prompts?: { label: string; prompt: string }[] | null;

  // Management metadata
  category: "public" | "custom";
  created_by?: string | null;
  created_at?: string | null;
  creator_name?: string | null;
}

export interface CreateAgentRequest {
  name: string;
  description?: string;
  model?: string | null;
  tool_groups?: string[] | null;
  skills?: string[] | null;
  soul?: string;
}
