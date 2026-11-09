import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { enableSkill } from "./api";

import { loadSkills } from ".";

export function useSkills() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills"],
    queryFn: () => loadSkills(),
  });
  return { skills: data ?? [], isLoading, error };
}

export function useEnableSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      skillName,
      enabled,
    }: {
      skillName: string;
      enabled: boolean;
    }) => {
      await enableSkill(skillName, enabled);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (error) => {
      // DEF-2: without this the toggle switch sprang back with no signal —
      // the API layer used to swallow non-2xx as success, and even with that
      // fixed the mutation failed silently. enableSkill already produces a
      // user-facing message; only non-Error rejections need the fallback.
      toast.error(
        error instanceof Error ? error.message : "技能开关失败，请重试",
      );
    },
  });
}
