import { fetch } from "@/core/api/fetcher";
import { readFastApiErrorDetail } from "@/core/api/read-fastapi-error";
import { getBackendBaseURL } from "@/core/config";

import type { Skill } from "./type";

export async function loadSkills() {
  const skills = await fetch(`${getBackendBaseURL()}/api/skills`);
  const json = await skills.json();
  return json.skills as Skill[];
}

export async function enableSkill(skillName: string, enabled: boolean) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/${skillName}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        enabled,
      }),
    },
  );
  // DEF-2: a non-2xx here (e.g. 500 from a failed config write) used to be
  // parsed as a success body and silently swallowed by callers — the toggle
  // appeared to work but nothing was persisted.
  if (!response.ok) {
    throw new Error(await readFastApiErrorDetail(response, "技能开关保存失败"));
  }
  return response.json();
}

export interface InstallSkillRequest {
  thread_id: string;
  path: string;
}

export interface InstallSkillResponse {
  success: boolean;
  skill_name: string;
  message: string;
}

export async function installSkill(
  request: InstallSkillRequest,
): Promise<InstallSkillResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/skills/install`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    return {
      success: false,
      skill_name: "",
      message: await readFastApiErrorDetail(response, "Failed to create skill"),
    };
  }

  return response.json();
}
