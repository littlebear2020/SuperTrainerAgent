import coreWebVitals from "eslint-config-next/core-web-vitals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      ".next",
      "src/components/ui/**",
      "src/components/ai-elements/**",
      "*.js",
    ],
  },
  // eslint-config-next 16 起只出 flat 预设（经 FlatCompat 装载旧命名会
  // circular-structure 崩溃，2026-10-05 C6 升级时迁移为直连）
  ...coreWebVitals,
  {
    files: ["**/*.ts", "**/*.tsx"],
    extends: [
      ...tseslint.configs.recommended,
      ...tseslint.configs.recommendedTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
    ],
    rules: {
      "@next/next/no-img-element": "off",
      // react-hooks v6 编译器规则 ratchet（2026-10-17 PR-1）：7 条全部 error 档，
      // 存量命中由根目录 eslint-suppressions.json 收编（CLI-only 机制：文件在
      // frontend/ 根即被 eslint 自动加载，config 无「引用」项；且只收编 severity=2
      // 条目——exhaustive-deps 须显式升 error 才收得进去）。新违规 = 新 error 直接红；
      // 清偿存量后跑 `pnpm exec eslint . --ext .ts,.tsx --prune-suppressions` 收尾
      // （残留已不再出现的豁免条目会 exit 2 炸门禁，防止豁免表变垃圾场）。
      "react-hooks/set-state-in-effect": "error",
      "react-hooks/refs": "error",
      "react-hooks/immutability": "error",
      "react-hooks/purity": "error",
      "react-hooks/globals": "error",
      "react-hooks/preserve-manual-memoization": "error",
      "react-hooks/exhaustive-deps": "error",
      "@typescript-eslint/array-type": "off",
      "@typescript-eslint/consistent-type-definitions": "off",
      "@typescript-eslint/consistent-type-imports": [
        "warn",
        { prefer: "type-imports", fixStyle: "inline-type-imports" },
      ],
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/require-await": "off",
      "@typescript-eslint/no-empty-object-type": "off",
      "@typescript-eslint/no-misused-promises": [
        "error",
        { checksVoidReturn: { attributes: false } },
      ],
      "@typescript-eslint/no-redundant-type-constituents": "off",
      "@typescript-eslint/no-unsafe-assignment": "off",
      "@typescript-eslint/no-unsafe-call": "off",
      "@typescript-eslint/no-unsafe-member-access": "off",
      "@typescript-eslint/no-unsafe-argument": "off",
      "@typescript-eslint/no-unsafe-return": "off",
      "import/order": [
        "error",
        {
          distinctGroup: false,
          groups: [
            "builtin",
            "external",
            "internal",
            "parent",
            "sibling",
            "index",
            "object",
          ],
          pathGroups: [
            {
              pattern: "@/**",
              group: "internal",
            },
            {
              pattern: "./**.css",
              group: "object",
            },
            {
              pattern: "**.md",
              group: "object",
            },
          ],
          "newlines-between": "always",
          alphabetize: {
            order: "asc",
            caseInsensitive: true,
          },
        },
      ],
    },
  },
  {
    linterOptions: {
      reportUnusedDisableDirectives: true,
    },
    languageOptions: {
      parserOptions: {
        projectService: true,
      },
    },
  },
);
