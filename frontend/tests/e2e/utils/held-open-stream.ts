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
 */
export async function startHeldOpenStreamServer(): Promise<HeldOpenStreamServer> {
  const responses = new Set<{ write: (chunk: string) => void }>();

  const server = createServer((_request, response) => {
    response.writeHead(200, {
      "Cache-Control": "no-cache",
      "Content-Type": "text/event-stream",
      "Access-Control-Allow-Origin": "*",
    });
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
