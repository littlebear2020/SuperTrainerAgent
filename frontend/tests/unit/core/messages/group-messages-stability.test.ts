/**
 * P1-5 下半：消息组每次渲染都重算。
 *
 * 根因（已核实）：`message-list.tsx:205` 在 JSX 里**内联**调用
 * `groupMessages(...)`，所以父组件任何一次重渲染（gallery 设置到位、
 * token 开关、流式指示器跳动……）都会重新分组整个消息列表，产出**全新的
 * group 对象**；`MessageGroup` 又没有 memo，于是每个组连带整棵子树重渲染。
 *
 * 光把 `groupMessages` 塞进 useMemo 不够：`useMemo` 只在 `messages` 引用不变
 * 时有效，而流式期间引用**必然**变。真正止血的是「内容没变的组复用上一次的
 * 对象引用」，这样 memo 化的 `MessageGroup` 才能跳过。
 *
 * 这里钉住那条复用规则的纯函数部分（`getMessageGroups` + `reuseMessageGroups`）；
 * 组件层的 memo 生效证明在 message-group-memo.dom.test.tsx。
 */

import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, test } from "vitest";

import { getMessageGroups, reuseMessageGroups } from "@/core/messages/utils";

function ai(id: string, content: string): Message {
  return { type: "ai", id, content } as Message;
}

function human(id: string, content: string): Message {
  return { type: "human", id, content } as Message;
}

describe("getMessageGroups", () => {
  test("返回分组数组本身（分组的单一实现）", () => {
    const groups = getMessageGroups([human("h1", "hi"), ai("a1", "yo")]);
    expect(groups.map((g) => g.type)).toEqual(["human", "assistant"]);
  });
});

describe("reuseMessageGroups", () => {
  // 复用判据是**消息对象引用**。这与真实链路一致：`mergeMessages` 只做数组
  // 过滤与拼接，不克隆消息对象，所以同一条消息跨渲染保持同一引用；变化的只有
  // 外层数组。故这些用例复用同一批消息对象，只重新分组。
  test("内容未变 → 逐组复用上一次的对象引用", () => {
    const h1 = human("h1", "hi");
    const a1 = ai("a1", "yo");
    const first = reuseMessageGroups([], getMessageGroups([h1, a1]), false);
    // 新数组、同一批消息对象（模拟父组件重渲染）。
    const second = reuseMessageGroups(first, getMessageGroups([h1, a1]), false);

    expect(second.length).toBe(first.length);
    second.forEach((group, index) => {
      expect(group).toBe(first[index]);
    });
  });

  test("某组内容变了 → 只有那一组换新引用，其余仍复用", () => {
    const h1 = human("h1", "hi");
    const first = reuseMessageGroups(
      [],
      getMessageGroups([h1, ai("a1", "yo")]),
      false,
    );
    // 新的 AI 消息对象（内容改了）→ 该组必须换新引用。
    const second = reuseMessageGroups(
      first,
      getMessageGroups([h1, ai("a1", "yo 改了")]),
      false,
    );

    expect(second[0]).toBe(first[0]);
    expect(second[1]).not.toBe(first[1]);
  });

  test("追加一条消息 → 已有组不受影响", () => {
    const h1 = human("h1", "hi");
    const first = reuseMessageGroups([], getMessageGroups([h1]), false);
    const second = reuseMessageGroups(
      first,
      getMessageGroups([h1, ai("a1", "新回答")]),
      false,
    );

    expect(second[0]).toBe(first[0]);
    expect(second.length).toBe(2);
  });

  test("流式期间最后一组永不复用——SDK 会原地改消息对象追加 token", () => {
    const h1 = human("h1", "hi");
    const a1 = ai("a1", "半句");
    const first = reuseMessageGroups([], getMessageGroups([h1, a1]), true);
    // 消息对象引用完全相同（SDK 原地追加 token 就是这个形状）：
    // 末组仍必须给新引用，否则流式输出卡住不动。
    const second = reuseMessageGroups(first, getMessageGroups([h1, a1]), true);

    expect(second[0]).toBe(first[0]);
    expect(second[1]).not.toBe(first[1]);
  });

  test("消息数量收缩（压缩/删除）不会串位复用", () => {
    const h1 = human("h1", "a");
    const a1 = ai("a1", "b");
    const h2 = human("h2", "c");
    const first = reuseMessageGroups([], getMessageGroups([h1, a1, h2]), false);
    const second = reuseMessageGroups(first, getMessageGroups([h2]), false);

    expect(second.length).toBe(1);
    // 唯一剩下的组是 h2，不能错配成 first[0]（h1）。
    expect(second[0]?.messages[0]?.id).toBe("h2");
    expect(second[0]).not.toBe(first[0]);
  });

  test("原地 mutate（SDK 追加 token）——引用相同也必须换新引用（B2）", () => {
    // SDK 流式期间在同一消息对象上原地追加 token：对象引用不变，content 变长。
    // 引用相等判据看不出这个变化，会误判"可复用" → memo 跳过 → 内容冻结。
    // 内容指纹（长度）能抓住它。非末组场景（streamSubgraphs 下中段组也会 mutate）。
    const h1 = human("h1", "hi");
    const a1 = ai("a1", "半句");
    const first = reuseMessageGroups([], getMessageGroups([h1, a1]), false);

    // 原地改 content（refs 不变），模拟 SDK 追加 token。
    a1.content = "半句话已经写完了";
    const second = reuseMessageGroups(first, getMessageGroups([h1, a1]), false);

    expect(second[0]).toBe(first[0]); // human 组未变，仍复用
    expect(second[1]).not.toBe(first[1]); // assistant 组 content 变了 → 必须换新引用
  });
});

// PR-2 4.1：组 id 主键 = message.id——历史分页 prepend 后旧组 id 不变
// （虚拟化测量缓存/滚动锚定的前提）；同 id 重复（lc_run 复用）追加 #dup-N。
describe("getMessageGroups 组 id 稳定性（PR-2 4.1）", () => {
  test("头部 prepend 新消息后，旧组的 id 原样保留", () => {
    const h1 = human("h1", "hi");
    const a1 = ai("a1", "yo");
    const before = getMessageGroups([h1, a1]).map((g) => g.id);
    // 分页加载：前面多了一条更早的历史消息，h1/a1 对象引用不变
    const older = human("h0", "earlier");
    const after = getMessageGroups([older, h1, a1]).map((g) => g.id);

    expect(after.slice(1)).toEqual(before);
  });

  test("同 id 重复（lc_run 复用形态）追加 #dup-N，仍全局唯一", () => {
    const groups = getMessageGroups([
      human("dup-id", "q1"),
      ai("dup-id", "a1"),
      ai("dup-id", "a2"),
    ]);
    const ids = groups.map((g) => g.id);
    expect(ids[0]).toBe("dup-id");
    expect(
      ids.slice(1).every((id) => (id ?? "").startsWith("dup-id#dup-")),
    ).toBe(true);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
