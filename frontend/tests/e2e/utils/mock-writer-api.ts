import { type Page, type Route } from "@playwright/test";

export interface MockWriterDoc {
  id: string;
  title: string;
  content_json: string;
  doc_type?: string;
  version: number;
}

export interface MockDocVersion {
  id: string;
  doc_id: string;
  version_number: number;
  title: string;
  snapshot_reason: string;
  created_at: string;
}

export interface RecordedWriterRequest {
  method: string;
  url: string;
  body: unknown;
}

export interface MockWriterAPI {
  readonly updateRequests: RecordedWriterRequest[];
  doc(docId: string): MockWriterDoc | undefined;
  updateDoc(doc: MockWriterDoc): void;
  setVersions(docId: string, versions: MockDocVersion[]): void;
}

function now(): number {
  return Date.now();
}

function writerJson(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function normalizeDoc(doc: MockWriterDoc) {
  return {
    id: doc.id,
    user_id: "e2e-user",
    title: doc.title,
    content_json: doc.content_json,
    doc_type: doc.doc_type ?? "general",
    version: doc.version,
    created_at: new Date(now() - 60_000).toISOString(),
    updated_at: new Date(now() - 30_000).toISOString(),
    deleted_at: null,
  };
}

/** Stateful /api/writer/docs mock. List response always uses {items, has_more}. */
export function mockWriterAPI(
  page: Page,
  initialDocs: MockWriterDoc[],
): MockWriterAPI {
  const docs = new Map(initialDocs.map((doc) => [doc.id, { ...doc }]));
  const versions = new Map<string, MockDocVersion[]>();
  const updateRequests: RecordedWriterRequest[] = [];

  void page.route("**/api/v1/auth/csrf-token", (route) =>
    writerJson(route, 200, { token: "mock-csrf-token" }),
  );

  void page.route("**/api/writer/docs/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const match = /^\/api\/writer\/docs\/([^/]+)(\/.*)?$/.exec(path);
    if (!match) return route.fallback();
    const docId = decodeURIComponent(match[1] ?? "");
    const suffix = match[2] ?? "";
    const doc = docs.get(docId);

    if (suffix === "/versions" && request.method() === "GET") {
      return writerJson(route, 200, versions.get(docId) ?? []);
    }

    if (
      suffix?.startsWith("/versions/") &&
      suffix.endsWith("/restore") &&
      request.method() === "POST"
    ) {
      if (!doc) return writerJson(route, 404, { detail: "Document not found" });
      return writerJson(route, 200, normalizeDoc(doc));
    }

    if (!suffix && request.method() === "GET") {
      if (!doc) return writerJson(route, 404, { detail: "Document not found" });
      return writerJson(route, 200, normalizeDoc(doc));
    }

    if (!suffix && request.method() === "PUT") {
      const body = JSON.parse(request.postData() ?? "{}") as {
        title?: string;
        content_json?: string;
        doc_type?: string;
      };
      updateRequests.push({
        method: request.method(),
        url: path,
        body,
      });
      if (!doc) return writerJson(route, 404, { detail: "Document not found" });
      doc.title = body.title ?? doc.title;
      doc.content_json = body.content_json ?? doc.content_json;
      doc.doc_type = body.doc_type ?? doc.doc_type;
      doc.version += 1;
      return writerJson(route, 200, normalizeDoc(doc));
    }

    return route.fallback();
  });

  void page.route(/\/api\/writer\/docs(?:\?.*)?$/, async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      return writerJson(route, 200, {
        items: [...docs.values()].map(normalizeDoc),
        has_more: false,
      });
    }

    if (request.method() === "POST") {
      const body = JSON.parse(request.postData() ?? "{}") as MockWriterDoc;
      const doc: MockWriterDoc = {
        id: `doc-${docs.size + 1}`,
        title: body.title,
        content_json: body.content_json ?? "",
        doc_type: body.doc_type,
        version: 1,
      };
      docs.set(doc.id, doc);
      return writerJson(route, 200, normalizeDoc(doc));
    }

    return route.fallback();
  });

  return {
    updateRequests,
    doc(docId: string) {
      return docs.get(docId);
    },
    updateDoc(doc: MockWriterDoc) {
      docs.set(doc.id, { ...doc });
    },
    setVersions(docId: string, nextVersions: MockDocVersion[]) {
      versions.set(docId, nextVersions);
    },
  };
}
