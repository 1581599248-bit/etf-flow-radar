import { cp, mkdir, readFile, writeFile } from "node:fs/promises";

const out = new URL("../pages-dist/", import.meta.url);
const page = await readFile(new URL("../pages/index.html", import.meta.url), "utf8");
const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
const htmlToImage = await readFile(new URL("../node_modules/html-to-image/dist/html-to-image.js", import.meta.url), "utf8");

await mkdir(out, { recursive: true });
await writeFile(new URL("index.html", out), page, "utf8");
await writeFile(new URL("styles.css", out), css.replace('@import "tailwindcss";', ""), "utf8");
await writeFile(new URL("html-to-image.js", out), htmlToImage, "utf8");
await cp(new URL("../public/data/", import.meta.url), new URL("data/", out), { recursive: true });
await writeFile(new URL(".nojekyll", out), "", "utf8");

console.log(`GitHub Pages bundle created at ${out.pathname}`);
