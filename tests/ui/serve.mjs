import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("../../src/tender_intelligence/admin/static/", import.meta.url)));
const mime = { ".html": "text/html; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8" };
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, "http://127.0.0.1").pathname;
  const relative = pathname === "/admin/" || pathname === "/admin" ? "index.html" : pathname.replace(/^\/admin\//, "");
  const target = resolve(root, relative);
  if (target !== root && !target.startsWith(`${root}${sep}`)) {
    response.writeHead(404).end();
    return;
  }
  try {
    const body = await readFile(target);
    response.writeHead(200, { "content-type": mime[extname(target)] || "application/octet-stream", "cache-control": "no-store" }).end(body);
  } catch {
    response.writeHead(404).end("Not found");
  }
});
server.listen(4173, "127.0.0.1", () => process.stdout.write("Offline Admin UI fixture server listening at http://127.0.0.1:4173/admin/\n"));
