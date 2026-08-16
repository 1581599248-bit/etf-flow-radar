import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";

const out = new URL("../dist/", import.meta.url);
let page = await readFile(new URL("../site/index.html", import.meta.url), "utf8");
const css = await readFile(new URL("../site/styles.css", import.meta.url), "utf8");
const htmlToImage = await readFile(new URL("../node_modules/html-to-image/dist/html-to-image.js", import.meta.url), "utf8");

// The source dashboard predates the clarified multi-day methodology.  Keep the
// visual layout unchanged while making the built client copy match the data
// contract: 5d/20d are endpoint share-change estimates, not sums of daily cash
// flows.  This normalization is intentionally centralized in the build step so
// GitHub Pages and Render receive identical wording.
const textReplacements = [
  ["5日累计资金变化", "5日端点资金变化"],
  ["20日累计资金变化", "20日端点资金变化"],
  ["5日累计", "5日端点变化"],
  ["近5日资金方向", "5日端点资金方向"],
  ["近5日净流入", "5日端点净流入"],
  ["近5日净流出", "5日端点净流出"],
  ["资金为组内ETF近5日净流入/流出", "资金为组内ETF的5日端点份额变化估算"],
  ["北京时间0:15、0:30、1:00、5:00、6:00、7:00和8:00", "北京时间5:00、6:00、7:00和8:00"],
  ["北京时间0:15、0:30、1:00、5:00、6:00、7:00、8:00", "北京时间5:00、6:00、7:00、8:00"],
];
for (const [from, to] of textReplacements) page = page.replaceAll(from, to);

await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await writeFile(new URL("index.html", out), page, "utf8");
await writeFile(new URL("styles.css", out), css, "utf8");
await writeFile(new URL("html-to-image.js", out), htmlToImage, "utf8");
await cp(new URL("../site/data/", import.meta.url), new URL("data/", out), { recursive: true });
await cp(new URL("../site/favicon.svg", import.meta.url), new URL("favicon.svg", out));
await writeFile(new URL(".nojekyll", out), "", "utf8");

console.log(`Static site bundle created at ${out.pathname}`);
