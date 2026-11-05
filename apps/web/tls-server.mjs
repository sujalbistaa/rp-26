// Next's standalone server has no TLS, so this terminates it inside the container. Next itself
// listens on loopback only, which keeps the hop from the edge proxy encrypted.
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import http from "node:http";
import https from "node:https";

const TLS_PORT = Number(process.env.TLS_PORT ?? 3100);
const NEXT_PORT = Number(process.env.NEXT_PORT ?? 3101);

if (process.env.NEXT_COMMAND !== "none") {
  const next = spawn("node", ["server.js"], {
    stdio: "inherit",
    env: { ...process.env, HOSTNAME: "127.0.0.1", PORT: String(NEXT_PORT) },
  });
  next.on("exit", (code) => process.exit(code ?? 1));
}

const server = https.createServer(
  {
    key: readFileSync(process.env.TLS_KEY ?? "/certs/web.key"),
    cert: readFileSync(process.env.TLS_CERT ?? "/certs/web.crt"),
    minVersion: "TLSv1.3",
  },
  (request, response) => {
    const upstream = http.request(
      {
        host: "127.0.0.1",
        port: NEXT_PORT,
        method: request.method,
        path: request.url,
        headers: request.headers,
      },
      (reply) => {
        response.writeHead(reply.statusCode ?? 502, reply.headers);
        reply.pipe(response);
      },
    );
    upstream.on("error", () => {
      response.writeHead(502);
      response.end();
    });
    request.pipe(upstream);
  },
);

server.listen(TLS_PORT, "0.0.0.0");
