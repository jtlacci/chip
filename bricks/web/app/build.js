// Build: copy src/ to dist/. The app is plain ES modules, so there is nothing to compile.
const fs = require("fs");
const path = require("path");

fs.rmSync("dist", { recursive: true, force: true });
fs.mkdirSync("dist", { recursive: true });
for (const name of fs.readdirSync("src")) {
  fs.copyFileSync(path.join("src", name), path.join("dist", name));
}
