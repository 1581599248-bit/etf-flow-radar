import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";

const out = new URL("../dist/", import.meta.url);
const page = await readFile(new URL("../site/index_v2.html", import.meta.url), "utf8");
const css = await readFile(new URL("../site/styles.css", import.meta.url), "utf8");
const cssV2 = await readFile(new URL("../site/v2.css", import.meta.url), "utf8");
const htmlToImage = await readFile(new URL("../node_modules/html-to-image/dist/html-to-image.js", import.meta.url), "utf8");

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await writeFile(new URL("index.html", out), page, "utf8");
await writeFile(new URL("styles.css", out), css, "utf8");
await writeFile(new URL("v2.css", out), cssV2, "utf8");
await writeFile(new URL("html-to-image.js", out), htmlToImage, "utf8");
await cp(new URL("../site/data/", import.meta.url), new URL("data/", out), { recursive: true });
await cp(new URL("../site/favicon.svg", import.meta.url), new URL("favicon.svg", out));
await writeFile(new URL(".nojekyll", out), "", "utf8");
console.log(`ETF taxonomy v2 static bundle created at ${out.pathname}`);
