import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("production bundle contains the real-data research product", async () => {
  const bundle = await readFile("dist/server/ssr/_next/static/page-BCeMHBd4.js", "utf8");
  assert.match(bundle, /资金ETF流动每日跟踪/);
  assert.match(bundle, /REAL DATA|真实数据/);
  assert.match(bundle, /不构成投资建议/);
  assert.doesNotMatch(bundle, /DEMO DATA/);
});
