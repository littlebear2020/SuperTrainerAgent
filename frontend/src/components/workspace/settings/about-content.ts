/**
 * About markdown content. Inlined to avoid raw-loader dependency
 * (Turbopack cannot resolve raw-loader for .md imports).
 */
export const aboutMarkdown = `# 关于本平台

> **从开源中来，回开源中去**

本平台是一个开源的 **超级 Agent 编排框架**，通过协调 **子 Agent**、**记忆系统** 和 **沙箱** 来完成复杂任务 — 由可扩展的 **技能系统** 驱动。

---

## 核心特性

* **技能与工具**：内置且可扩展的技能和工具系统，覆盖多种场景
* **子 Agent 协作**：子 Agent 帮助主 Agent 完成过于复杂的任务
* **沙箱与文件系统**：在沙箱中安全执行代码和操作文件
* **上下文工程**：隔离的子 Agent 上下文，自动摘要保持上下文窗口高效
* **长期记忆**：持续记录用户画像、关注事项和对话历史

---

## 开源

本平台基于 [DeerFlow](https://github.com/bytedance/deer-flow) 构建，以 **MIT 许可证** 开源。

---

## 致谢

我们向所有为本项目做出贡献的开源项目和开发者致以诚挚的感谢。

### 核心框架
- **[LangChain](https://github.com/langchain-ai/langchain)**：LLM 交互与链式调用的杰出框架
- **[LangGraph](https://github.com/langchain-ai/langgraph)**：多 Agent 编排引擎
- **[Next.js](https://nextjs.org/)**：Web 应用开发框架

### UI 组件
- **[Shadcn](https://ui.shadcn.com/)**：简约高效的 UI 组件库
`;
