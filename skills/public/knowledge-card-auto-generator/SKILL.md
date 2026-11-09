---
name: knowledge-card-auto-generator
version: 1.0.0
description: >
  从结构化 {title, points} 数据生成毛玻璃风格知识卡片。输出 SVG 静态图片和
  MP4 动画视频，支持批量生成、三种主题变体（科技蓝、霓虹紫、翡翠绿），
  以及带文字动画和发光效果的可定制卡片布局。当用户要求生成知识卡片、
  信息卡片、卡片海报时触发。
author: littlebear
license: MIT
metadata:
  openclaw:
    requires:
      bins:
        - node
    install:
      - type: node
        packages:
          - remotion
          - "@remotion/cli"
          - "@remotion/renderer"
          - "@remotion/bundler"
          - "@remotion/tailwind"
          - react
          - react-dom
          - framer-motion
          - commander
---

# Knowledge Card Auto-Generator

Generate beautiful glassmorphism-styled knowledge cards in SVG and MP4 format from structured data.

## Usage

**Primary path (pre-installed in sandbox image, no network required):**

```bash
node --import tsx /opt/knowledge-card/src/index.ts --input cards.json --format both --theme tech-blue --output ./output
```

**Fallback (仅本地开发，需要网络，sandbox 中不可用):**

```bash
cp -r /mnt/skills/public/knowledge-card-auto-generator /tmp/knowledge-card && cd /tmp/knowledge-card && npm install --no-audit --no-fund
node --import tsx /tmp/knowledge-card/src/index.ts --input cards.json --format both --theme tech-blue --output ./output
```

> Always prefer the primary path `/opt/knowledge-card/` first. Only use the fallback when the pre-installed version is unavailable.

## Input Format

Provide a JSON file with an array of card objects:

```json
[
  {
    "id": "react-hooks",
    "title": "React Hooks",
    "subtitle": "Essential patterns for modern React",
    "points": [
      "useState for local state management",
      "useEffect for side effects and cleanup",
      "useCallback for memoized callbacks",
      "useMemo for expensive computations"
    ],
    "theme": "tech-blue",
    "tags": ["react", "hooks", "frontend"]
  },
  {
    "id": "typescript-basics",
    "title": "TypeScript Essentials",
    "points": [
      "Static type checking at compile time",
      "Interfaces and type aliases",
      "Generics for reusable code"
    ],
    "theme": "neon-purple",
    "tags": ["typescript", "types"]
  }
]
```

### Card Fields

| Field      | Type     | Required | Description                                    |
|------------|----------|----------|------------------------------------------------|
| `id`       | string   | Yes      | Unique identifier (used for filenames)         |
| `title`    | string   | Yes      | Card title text                                |
| `subtitle` | string   | No       | Subtitle below the title                       |
| `points`   | string[] | Yes      | Array of bullet points                         |
| `theme`    | string   | No       | Theme: tech-blue, neon-purple, emerald-green   |
| `tags`     | string[] | No       | Array of tag labels shown at the bottom        |

## Output

- SVG files: `{outputDir}/svg/{id}.svg`
- MP4 files: `{outputDir}/mp4/{id}.mp4`

## Options

| Flag          | Description                                       | Default     |
|---------------|---------------------------------------------------|-------------|
| `--input`     | Path to JSON input file                            | (required)  |
| `--format`    | Output format: `svg`, `mp4`, or `both`            | `both`      |
| `--theme`     | Default theme: `tech-blue`, `neon-purple`, `emerald-green` | `tech-blue` |
| `--output`    | Output directory                                   | `./output`  |
| `--concurrency` | Parallel render limit for video                  | `2`         |

## Themes

### Tech Blue
Dark navy background with blue accent glassmorphism. Professional and modern.

### Neon Purple
Deep purple background with vibrant purple glow. Creative and bold.

### Emerald Green
Dark green background with emerald accents. Natural and clean.

## 叙事型长图风格规范（narrative-long）

适用于信息密度高、有故事线的知识长图（非简单要点列表卡片）。

### 背景
- 深紫到深蓝渐变（#1a0a2e → #0f172a → #0a1628）
- 带微弱光晕效果（radial-gradient，半透明蓝色/紫色椭圆）
- 叠加细网格纹理（rgba 白色线条，低透明度）
- 整体宽度 1080px，高度自适应内容

### 布局
- 垂直流式结构，各区块用圆角矩形包裹
- 区块之间用白色向下箭头（▼）连接，带发光效果
- 左右留白约 50-60px

### 配色系统
- 主文字：白色 #ffffff
- 副文字：rgba(255,255,255,0.6~0.7)
- 绿色（成功/正向）：#4ade80
- 蓝色（科技/链接）：#60a5fa / #3b82f6
- 紫色（强调）：#a78bfa
- 橙色（警告/瓶颈）：#fb923c
- 红色（危险/重点高亮）：#ef4444
- 卡片背景：rgba(255,255,255,0.04~0.06)
- 卡片边框：rgba(255,255,255,0.08~0.15)

### 标题区
- 粗体大字 + 外发光效果（text-shadow: 0 0 20px rgba(96,165,250,0.5)）
- 半透明深灰圆角矩形背景框，白色描边
- 底部渐变分隔线（蓝色→紫色→透明）

### 内容区
- 分帧式叙事结构（第一帧、第二帧…）
- 每帧有标签 badge + 标题
- 帧内可包含：对比图、流程图、进度条、终端模拟、对话气泡等
- 对话气泡：圆角矩形 + 三角箭头，半透明背景

### 装饰元素
- 毛玻璃半透明卡片（rgba 背景 + blur）
- neon 发光线条风格图标
- 圆角进度条（绿色填充 vs 橙色对比）
- macOS 风格窗口控制按钮（红黄绿圆点）用于终端模拟
- 星爆/爆炸图标表示冲突
- 滑块控件、警告三角等拟物 UI 元素

### 底部
- 关键结论框：深灰色圆角矩形，边框较粗（2px），加粗白色字体
- 可选：品牌水印（如需）

### 实现方式
- 使用 HTML + CSS 编写，通过系统 Chrome headless 截图为 PNG
- 不要用 playwright/puppeteer（需要下载浏览器，太慢）
- 截图命令：
  ```bash
  /opt/chrome-headless-shell/chrome-headless-shell \
    --headless=new --disable-gpu --no-sandbox \
    --screenshot=output.png --window-size=1080,2200 \
    file:///path/to/input.html
  ```

## Tech Stack

- **Remotion** - Programmatic video generation (SVG/MP4 简单卡片)
- **HTML + CSS** - 叙事型长图
- **Chrome Headless** - HTML 转 PNG 截图
- **Tailwind CSS** - Utility-first styling
- **Framer Motion** - Animation library
- **React** - Component-based rendering
