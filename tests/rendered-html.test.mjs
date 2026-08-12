import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("production bundle contains research product and demo disclosure", async () => {
  const html = await readFile("dist/client/index.html", "utf8");
  assert.match(html, /ETF资金雷达/);
  assert.match(html, /DEMO DATA/);
  assert.match(html, /不构成任何投资建议/);
});
