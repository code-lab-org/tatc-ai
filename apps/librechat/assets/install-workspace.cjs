// Run at container startup against the installed LibreChat index; idempotent.
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const filename = process.argv[2] || "/app/client/dist/index.html";
const configured = new URL(process.env.TATC_VIEWER_ORIGIN);
// LibreChat caches static assets for two days. Version the mounted files so a
// deployment cannot combine an updated viewer with a cached host adapter.
const hash = createHash("sha256");
for (const asset of ["tatc-workspace.js", "tatc-workspace.css"])
  hash.update(
    fs.readFileSync(path.join(path.dirname(filename), "assets", asset)),
  );
const version = hash.digest("hex").slice(0, 12);
if (
  !["https:", "http:"].includes(configured.protocol) ||
  configured.username ||
  configured.password
)
  throw new Error("TATC_VIEWER_ORIGIN must be an HTTP(S) origin");
const origin = configured.origin
  .replaceAll("&", "&amp;")
  .replaceAll('"', "&quot;");
const html = fs
  .readFileSync(filename, "utf8")
  .replace(
    /<!-- tatc-workspace:start -->[\s\S]*?<!-- tatc-workspace:end -->\s*/g,
    "",
  );
if (!html.includes("</body>"))
  throw new Error("LibreChat index has no closing body");
fs.writeFileSync(
  filename,
  html.replace(
    "</body>",
    `<!-- tatc-workspace:start --><link rel="stylesheet" href="/assets/tatc-workspace.css?v=${version}"><script defer src="/assets/tatc-workspace.js?v=${version}" data-viewer-origin="${origin}"></script><!-- tatc-workspace:end -->\n</body>`,
  ),
);
