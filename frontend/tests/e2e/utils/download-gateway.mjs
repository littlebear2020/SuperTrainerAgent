import { createReadStream } from "node:fs";
import { createServer } from "node:http";

const port = Number(process.env.E2E_GATEWAY_PORT ?? 3010);

const server = createServer((request, response) => {
  if (request.url === "/health") {
    response.writeHead(200, { "Content-Type": "text/plain" });
    response.end("ok");
    return;
  }
  if (
    request.method === "GET" &&
    request.url === "/api/video/tasks/playable/download"
  ) {
    const stream = createReadStream(
      `${process.cwd()}/public/course-samples/image-motion.mp4`,
    );
    stream.on("error", (error) => {
      console.error("E2E sample download failed:", error.message);
      if (response.headersSent) {
        response.destroy(error);
        return;
      }
      response.writeHead(500, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ detail: "Sample file unavailable" }));
    });
    stream.on("open", () => {
      response.writeHead(200, {
        "Content-Type": "video/mp4",
        "Content-Disposition": 'attachment; filename="playable.mp4"',
      });
      stream.pipe(response);
    });
    return;
  }
  response.writeHead(404, { "Content-Type": "application/json" });
  response.end(
    JSON.stringify({ detail: "Not mocked by the download gateway" }),
  );
});

server.listen(port, "127.0.0.1", () => {
  console.log(`E2E download gateway listening on ${port}`);
});
