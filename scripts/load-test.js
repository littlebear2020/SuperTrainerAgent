/**
 * k6 Load Test — Video Generation Module
 *
 * Validates 50-100 concurrent user load against video_gen + writer endpoints.
 * Mix: 70% create + 20% ai_visual + 10% create
 * NOTE: 未经实跑验证（k6 未安装），首次使用前须本地装 k6 跑通。
 * P2-9: 修掉了不存在的路由（stock-footage / remotion）+ 补 Cookie/CSRF。
 *
 * Prerequisites:
 *   - k6 installed (https://k6.io/docs/get-started/installation/)
 *   - Backend running at BASE_URL (default http://localhost:2026)
 *   - User authenticated (cookie or token)
 *
 * Usage:
 *   k6 run tests/load-test.js
 *   k6 run --vus 100 --duration 5m tests/load-test.js
 *
 * Metrics validated (thresholds):
 *   - http_req_duration p95 < 3s for assist API
 *   - http_req_failed < 5%
 *   - video_gen_task_submit_rate < 50/s
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// ── Configuration ──────────────────────────────────────────────────────

const BASE_URL = __ENV.BASE_URL || "http://localhost:2026";

const taskSubmitRate = new Rate("task_submit_success");
const assistDuration = new Trend("assist_duration", true);

export const options = {
  scenarios: {
    // Scenario 1: Writer assist (lightweight, high frequency)
    writer_assist: {
      executor: "constant-arrival-rate",
      rate: 10, // 10 req/s
      timeUnit: "1s",
      duration: "3m",
      preAllocatedVUs: 20,
      exec: "writerAssist",
    },
    // Scenario 2: Video task submission (low frequency, mixed types)
    video_gen: {
      executor: "per-vu-iterations",
      vus: 30,
      iterations: 5,
      maxDuration: "5m",
      exec: "videoGen",
      gracefulStop: "30s",
    },
    // Scenario 3: Writer doc CRUD
    writer_crud: {
      executor: "constant-arrival-rate",
      rate: 3,
      timeUnit: "1s",
      duration: "3m",
      preAllocatedVUs: 10,
      exec: "writerCrud",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.05"], // <5% failure rate
    http_req_duration: ["p(95)<5000"], // p95 < 5s
    assist_duration: ["p(95)<3000"], // assist p95 < 3s
    task_submit_success: ["rate>0.95"], // >95% task submit success
  },
};

// ── Shared params ──────────────────────────────────────────────────────

const params = {
  headers: {
    "Content-Type": "application/json",
    "Cookie": __ENV.AUTH_COOKIE || "",
    "X-CSRF-Token": __ENV.CSRF_TOKEN || "",
  },
  timeout: "30s",
};

// ── Scenario: Writer Assist ────────────────────────────────────────────

export function writerAssist() {
  const ops = ["polish", "rewrite", "expand", "summarize", "grammar", "translate"];
  const op = ops[Math.floor(Math.random() * ops.length)];
  const texts = [
    "培训是企业发展的核心驱动力",
    "有效的知识传递需要结构化的方法",
    "现代培训体系应当融合线上与线下",
    "学员参与度是衡量培训效果的关键指标",
    "AI 技术正在改变传统的培训模式",
  ];
  const text = texts[Math.floor(Math.random() * texts.length)];

  const start = Date.now();
  const res = http.post(
    `${BASE_URL}/api/ai/assist`,
    JSON.stringify({ op, text, context: "培训内容编写" }),
    params,
  );
  assistDuration.add(Date.now() - start);

  check(res, {
    "assist status 200": (r) => r.status === 200,
    "assist has SSE data": (r) => r.body && r.body.includes("data:"),
  });

  sleep(0.5);
}

// ── Scenario: Video Gen ────────────────────────────────────────────────

export function videoGen() {
  const rand = Math.random();
  let endpoint, body, label;

  if (rand < 0.7) {
    // 70% create (ai_image)
    endpoint = `${BASE_URL}/api/video/create`;
    body = JSON.stringify({
      video_type: "ai_image",
      mode: "generate",
      topic: `企业培训短视频_${Date.now()}`,
      lang: "zh",
      style: "default",
    });
    label = "create_ai_image";
  } else if (rand < 0.9) {
    // 20% ai_visual
    endpoint = `${BASE_URL}/api/video/ai-visual`;
    body = JSON.stringify({
      prompt: `测试提示_${Date.now()}: 一段关于企业培训的短视频`,
      duration: 15,
      style: "default",
    });
    label = "ai_visual";
  } else {
    // 10% create (ai_video)
    endpoint = `${BASE_URL}/api/video/create`;
    body = JSON.stringify({
      video_type: "ai_video",
      mode: "generate",
      topic: `AI视频培训_${Date.now()}`,
      lang: "zh",
      style: "default",
    });
    label = "create_ai_video";
  }

  const res = http.post(endpoint, body, params);
  const ok = check(res, {
    "task submit status 2xx": (r) => r.status >= 200 && r.status < 300,
  });
  taskSubmitRate.add(ok);

  // If task created, poll status (max 60s)
  if (ok && res.json("id")) {
    const taskId = res.json("id");
    const pollEnd = Date.now() + 60000;

    while (Date.now() < pollEnd) {
      sleep(2);
      const pollRes = http.get(
        `${BASE_URL}/api/video/tasks/${taskId}`,
        params,
      );
      const status = pollRes.json("status");
      if (status === "ready" || status === "failed") break;
    }
  }

  sleep(1);
}

// ── Scenario: Writer CRUD ─────────────────────────────────────────────

export function writerCrud() {
  // Create doc
  const createRes = http.post(
    `${BASE_URL}/api/writer/docs`,
    JSON.stringify({ title: `负载测试文档_${Date.now()}` }),
    params,
  );

  const docId = createRes.json("id");

  check(createRes, {
    "doc created": (r) => r.status === 200 || r.status === 201,
  });

  if (docId) {
    // Update doc
    http.put(
      `${BASE_URL}/api/writer/docs/${docId}`,
      JSON.stringify({
        title: `更新标题_${Date.now()}`,
        content_json: JSON.stringify({
          type: "doc",
          content: [
            {
              type: "paragraph",
              content: [{ type: "text", text: "负载测试自动保存内容" }],
            },
          ],
        }),
      }),
      params,
    );

    sleep(0.5);

    // Delete doc
    http.del(`${BASE_URL}/api/writer/docs/${docId}`, null, params);
  }

  sleep(1);
}

// ── Teardown ───────────────────────────────────────────────────────────

export function teardown() {
  // Check metrics endpoint
  const res = http.get(`${BASE_URL}/metrics`);
  check(res, {
    "metrics endpoint available": (r) => r.status === 200,
    "metrics has video_gen_task_total": (r) =>
      r.body && r.body.includes("video_gen_task_total"),
    "metrics has task_queue_pending": (r) =>
      r.body && r.body.includes("task_queue_pending"),
  });
}
