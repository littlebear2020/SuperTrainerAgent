/**
 * Per-type starter templates (W30 — §2 "模板做实").
 *
 * New docs are seeded with a type-appropriate markdown structure so the
 * type choice has real meaning from creation. Each template's first line
 * is `# {title}` — consistent with W27's title-write-back (the autosave
 * derives the stored title from the first `# H1` heading via
 * `deriveTitleFromMarkdown`).
 *
 * `getStarterTemplate(docType, title)` fills the `{title}` placeholder.
 */

import type { DocType } from "./types";

/**
 * Starter markdown for each doc type. The `{title}` placeholder is
 * substituted by `getStarterTemplate`. Templates are intentionally
 * distinct — each reflects the structural conventions of its type.
 */
export const DOC_TYPE_TEMPLATES: Record<DocType, string> = {
  // 公众号文章 — 导语 + 三段正文 + 结语
  article: `# {title}

> 导语：用一句话点明核心观点，吸引读者继续阅读。

## 一、

## 二、

## 三、

---

（结语：呼应导语，给出行动建议或思考。）`,

  // 商业案例 — 背景/问题/方案/结果/启示
  case: `# {title}

## 背景

（业务/项目所处的环境与前提。）

## 问题

（面临的核心挑战与痛点。）

## 方案

（采取的关键举措与实施路径。）

## 结果与数据

（量化成果、对比基线。）

## 启示

（可复用的经验与注意事项。）`,

  // 视频脚本 — 分场景，画面/旁白/时长
  script: `# {title}

## 场景 1

- 画面：
- 旁白：
- 时长：

## 场景 2

- 画面：
- 旁白：
- 时长：

## 场景 3

- 画面：
- 旁白：
- 时长：`,

  // 语音博客文稿 — 话题/嘉宾/对话
  podcast: `# {title}

## 话题

（本期讨论的核心议题。）

## 嘉宾

- 主持人：
- 嘉宾：

## 对话

- 主持人：
- 嘉宾：
- 主持人：
- 嘉宾：`,

  // 技术分享课程 — 教学目标 + 知识点（要点/示例/练习）
  course: `# {title}

> 🎯 教学目标：学完后能够……

## 知识点 1

### 要点

### 示例

### 练习

## 知识点 2

### 要点

### 示例

### 练习`,

  // 通用写作 — 最小结构，仅 H1
  general: `# {title}

`,
};

/**
 * Returns the starter template for `docType` with `{title}` filled in.
 *
 * Falls back to the `general` template if `docType` is not recognized
 * (defensive — should not happen in normal flow).
 */
export function getStarterTemplate(docType: DocType, title: string): string {
  const template = DOC_TYPE_TEMPLATES[docType] ?? DOC_TYPE_TEMPLATES.general;
  // Use a function replacement to avoid `$` special-pattern interpretation
  // if `title` happens to contain `$1` / `$$` etc.
  return template.replace(/\{title\}/g, () => title);
}
