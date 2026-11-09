"use client";

import { SparklesIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Item,
  ItemActions,
  ItemTitle,
  ItemContent,
  ItemDescription,
} from "@/components/ui/item";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsConsoleAdmin } from "@/core/console/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { useEnableSkill, useSkills } from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";
import { env } from "@/env";

import { SettingsSection } from "./settings-section";

export function SkillSettingsPage({ onClose }: { onClose?: () => void } = {}) {
  const { t } = useI18n();
  const { skills, isLoading, error } = useSkills();
  return (
    <SettingsSection
      title={t.settings.skills.title}
      description={t.settings.skills.description}
    >
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : error ? (
        <div>Error: {error.message}</div>
      ) : (
        <SkillSettingsList skills={skills} onClose={onClose} />
      )}
    </SettingsSection>
  );
}

function SkillSettingsList({
  skills,
  onClose,
}: {
  skills: Skill[];
  onClose?: () => void;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const [filter, setFilter] = useState<string>("public");
  const { mutate: enableSkill } = useEnableSkill();
  const isAdmin = useIsConsoleAdmin();
  const filteredSkills = useMemo(
    () => skills.filter((skill) => skill.category === filter),
    [skills, filter],
  );
  // 收尾复核批（2026-10-07）：后端 _check_skill_ownership（SEC-M22）对 legacy
  // 类技能要求 admin——非 admin 点开关必然 403。可点的开关本身就是误导，
  // 禁用 + 行内提示（不走 tooltip：disabled 元素上的 hover 提示浏览器行为
  // 不可靠，行内文案确定可见且可测）。
  const legacyLocked = filter === "legacy" && !isAdmin;
  const handleCreateSkill = () => {
    onClose?.();
    router.push("/workspace/chats/new?mode=skill");
  };
  return (
    <div className="flex w-full flex-col gap-4">
      <header className="flex justify-between">
        <div className="flex gap-2">
          <Tabs defaultValue="public" onValueChange={setFilter}>
            <TabsList variant="line">
              <TabsTrigger value="public">{t.common.public}</TabsTrigger>
              <TabsTrigger value="custom">{t.common.custom}</TabsTrigger>
              {/* OBS-1（D1）：旧全局 custom 技能自 P1-S3 起以 LEGACY 只读兜底，
                  此前 UI 无入口——用户只能靠猜「为什么技能不见了」。只读展示 +
                  启停（PUT /api/skills/{name} 的 LEGACY 分支已落 per-user 状态）。 */}
              <TabsTrigger value="legacy">{t.common.legacy}</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <div>
          <Button size="sm" onClick={handleCreateSkill}>
            <SparklesIcon className="size-4" />
            {t.settings.skills.createSkill}
          </Button>
        </div>
      </header>
      {filteredSkills.length === 0 && filter === "legacy" && <EmptySkill />}
      {filteredSkills.length === 0 && filter !== "legacy" && (
        <EmptySkill onCreateSkill={handleCreateSkill} />
      )}
      {legacyLocked && filteredSkills.length > 0 && (
        <p className="text-muted-foreground text-xs">
          {t.settings.skills.legacyAdminOnlyHint}
        </p>
      )}
      {filteredSkills.length > 0 &&
        filteredSkills.map((skill) => (
          <Item className="w-full" variant="outline" key={skill.name}>
            <ItemContent>
              <ItemTitle>
                <div className="flex items-center gap-2">{skill.name}</div>
              </ItemTitle>
              <ItemDescription className="line-clamp-4">
                {skill.description}
              </ItemDescription>
            </ItemContent>
            <ItemActions>
              <Switch
                checked={skill.enabled}
                disabled={
                  env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" || legacyLocked
                }
                onCheckedChange={(checked) =>
                  enableSkill({ skillName: skill.name, enabled: checked })
                }
              />
            </ItemActions>
          </Item>
        ))}
    </div>
  );
}

function EmptySkill({ onCreateSkill }: { onCreateSkill?: () => void }) {
  const { t } = useI18n();
  // OBS-1：legacy tab 的空态不带创建 CTA（旧全局技能不是用户创建入口的产物），
  // 文案单独一套（emptyLegacy*）；onCreateSkill 缺省即 legacy 形态。
  const legacy = onCreateSkill === undefined;
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <SparklesIcon />
        </EmptyMedia>
        <EmptyTitle>
          {legacy
            ? t.settings.skills.emptyLegacyTitle
            : t.settings.skills.emptyTitle}
        </EmptyTitle>
        <EmptyDescription>
          {legacy
            ? t.settings.skills.emptyLegacyDescription
            : t.settings.skills.emptyDescription}
        </EmptyDescription>
      </EmptyHeader>
      {!legacy && (
        <EmptyContent>
          <Button onClick={onCreateSkill}>
            {t.settings.skills.emptyButton}
          </Button>
        </EmptyContent>
      )}
    </Empty>
  );
}
