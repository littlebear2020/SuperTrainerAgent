import { createServer } from "node:http";
import { type AddressInfo } from "node:net";

export interface HeldOpenStreamServer {
  url: string;
  writeFrame(frame: string): void;
  close(): Promise<void>;
}

/**
 * A real SSE server whose connection remains open after writing frames.
 * Tests bridge browser requests with `route.continue({ url: server.url })`.
 *
 * `echoContentLocation`：请求路径形如 `/threads/{thread}/runs/{run}` 时，在响应头
 * 回显 `Content-Location: /threads/{thread}/runs/{run}`。LangGraph SDK 只凭这个
 * 响应头触发 `onRunCreated`（metadata 事件不算），受理点测试需要它。
 */
export async function startHeldOpenStreamServer(options?: {
  echoContentLocation?: boolean;
}): Promise<HeldOpenStreamServer> {
  const responses = new Set<{ write: (chunk: string) => void }>();

  const server = createServer((request, response) => {
    const runMatch =
      /\/threads\/(?<thread>[^/?#]+)\/runs\/(?<run>[^/?#]+)/.exec(
        request.url ?? "",
      );
    // 开了回显却没带 `/threads/{id}/runs/{run}`：SDK 永远不会触发 onCreated，
    // 用例只会以超时失败——在服务端直接暴露误用
    if (options?.echoContentLocation && !runMatch) {
      response.destroy(
        new Error(
          `held-open stream: echoContentLocation needs a /threads/{id}/runs/{run} path, got ${request.url}`,
        ),
      );
      return;
    }
    response.writeHead(200, {
      "Cache-Control": "no-cache",
      "Content-Type": "text/event-stream",
      "Access-Control-Allow-Origin": "*",
      // 跨源响应默认只暴露 CORS 安全清单里的头；SDK 靠 Content-Location 触发 onCreated
      "Access-Control-Expose-Headers": "Content-Location",
      ...(options?.echoContentLocation && runMatch?.groups
        ? {
            "Content-Location": `/threads/${runMatch.groups.thread}/runs/${runMatch.groups.run}`,
          }
        : {}),
    });
    // writeHead 不会立刻发出响应头（要等首帧）：持有连接不写帧时 SDK 收不到
    // Content-Location，onCreated 就不会触发——这里显式 flush。
    response.flushHeaders();
    const holder = {
      write: (chunk: string) => {
        if (!response.destroyed) response.write(chunk);
      },
    };
    responses.add(holder);
    response.on("close", () => responses.delete(holder));
  });

  await new Promise<void>((resolve, reject) => {
    const onError = (error: Error) => reject(error);
    server.once("error", onError);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", onError);
      resolve();
    });
  });

  const address = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${address.port}/stream`,
    writeFrame(frame: string) {
      for (const response of responses) response.write(frame);
    },
    async close() {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    },
  };
}
