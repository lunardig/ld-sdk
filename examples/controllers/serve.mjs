// Serves the sample controllers, with the SDK's ldcp client beside them:
//   (cd ts && npm run build) && node examples/controllers/serve.mjs [port]
// then open http://localhost:<port>/spreadsheet.html?host=<host origin> from the host's
// Controllers dialog (http is allowed for localhost only).
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const client = fileURLToPath(new URL("../../ts/dist/src/ldcp.js", import.meta.url));
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };

export function serveControllers(port = 0, host = "127.0.0.1") {
  const server = createServer(async (req, res) => {
    const path = normalize(new URL(req.url, "http://x").pathname).replace(/^\/+/, "") || "index.html";
    const file = path === "ldcp.js" ? client : join(here, path);
    if (!file.startsWith(here) && file !== client) return res.writeHead(404).end();
    try {
      const body = await readFile(file);
      res.writeHead(200, { "content-type": types[extname(file)] ?? "application/octet-stream" }).end(body);
    } catch {
      res.writeHead(404).end();
    }
  });
  return new Promise((resolve) => server.listen(port, host, () => resolve(server)));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const s = await serveControllers(Number(process.argv[2] ?? 5174));
  console.log(`sample controllers at http://localhost:${s.address().port}/`);
}
