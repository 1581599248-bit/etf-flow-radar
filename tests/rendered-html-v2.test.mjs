import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const html = await readFile(new URL("../dist/index.html", import.meta.url), "utf8");
const css = await readFile(new URL("../dist/v2.css", import.meta.url), "utf8");

test("v2 client removes old ungrouped focus module", () => {
  assert.doesNotMatch(html, /未归组与热门ETF异动/);
  assert.doesNotMatch(html, /FOCUS ETF MOVES/);
});

test("v2 client exposes market taxonomy and ETF detail", () => {
  assert.match(html, /市场主题与行业资金/);
  assert.match(html, /单只ETF明细/);
  assert.match(html, /机器人、新能源、白酒、消费、AI算力/);
  assert.match(html, /displayName\(r\)/);
});

test("v2 client requires schema version 8", () => {
  assert.match(html, /schemaVersion<8/);
});

test("responsive ETF table styles are bundled", () => {
  assert.match(css, /\.etf-row/);
  assert.match(css, /@media\(max-width:760px\)/);
});
