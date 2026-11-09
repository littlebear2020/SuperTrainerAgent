import { resolve } from "path";

import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  test: {
    // 2026-10-17 PR-1 分层（对标上游 rstest 双 project 形态）：
    // node = 纯逻辑默认档；dom = 需 document/renderHook 的 happy-dom 档。
    // 命名约定：dom 档测试文件名带 `.dom.` 中缀（hooks 经 renderHook 驱动、
    // 组件渲染一律落 dom 档；不 render 的测试不得进 dom 档——DOM 环境约 3x 开销）。
    // inline project 不自动继承根配置，须显式 extends: true 拿 alias/coverage。
    projects: [
      {
        extends: true,
        test: {
          name: "node",
          include: [
            "tests/unit/**/*.test.ts",
            "tests/unit/**/*.test.tsx",
            "src/**/*.test.ts",
            "src/**/*.test.tsx",
          ],
          exclude: [
            ...configDefaults.exclude,
            "**/*.dom.test.ts",
            "**/*.dom.test.tsx",
          ],
        },
      },
      {
        extends: true,
        test: {
          name: "dom",
          environment: "happy-dom",
          include: [
            "tests/unit/**/*.dom.test.ts",
            "tests/unit/**/*.dom.test.tsx",
            "src/**/*.dom.test.ts",
            "src/**/*.dom.test.tsx",
          ],
        },
      },
    ],
    // tests/unit/ 镜像 src/ 布局为主约定；src/ 下 colocated 测试纳入收集
    // （2026-08-19 五份反馈批次：计划的测试路径与回归命令按 src 路径书写）。
    // 2026-10-05 基建批：覆盖率可见性（pnpm test:cov）——只报告不设阈值，
    // 用于找防护盲区；ui/ai-elements 为 registry 生成物不计量，路由壳由 e2e 覆盖。
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/components/ui/**",
        "src/components/ai-elements/**",
        "src/app/**/layout.tsx",
        "src/app/**/page.tsx",
      ],
    },
  },
});
