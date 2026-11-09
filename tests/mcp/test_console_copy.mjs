import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { chromium } from "playwright";

const html = await readFile(new URL("../../src/loommux/mcp/console.html", import.meta.url), "utf8");
const mainName = ` 主 kernel "<>&' 😮 `;
const childName = `子 kernel "<>&' ${"长名字".repeat(65)}`;
const resource = (id, name, scope = "main", home = null) => ({
  resource_id: id, name, scope, home, leases: [], busy: false,
  lifecycle: "running", kernel_state: "running", kernel_pid: 123,
  current_execution: null, execution_count: 1, retention_remaining_seconds: null,
});
const snapshot = {
  policy: {
    generation: 1, mode: "connection", private_activity_timeout_seconds: 4800,
    named_activity_timeout_seconds: 86400, heartbeat_interval_seconds: 15,
    heartbeat_timeout_seconds: 60, connection_grace_seconds: 60,
  },
  resources: [
    resource("main", mainName),
    resource("child", childName, "child", mainName),
    resource("stray", "孤立子 kernel", "child"),
  ],
};
const mutations = [];
const server = createServer((request, response) => {
  if (request.method !== "GET") {
    mutations.push(request.url);
    response.writeHead(405).end();
  } else if (request.url === "/api/resources") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(snapshot));
  } else {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(html);
  }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1100 },
    permissions: ["clipboard-read", "clipboard-write"],
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`, { timeout: 10000 });
  await page.locator("[data-copy=main]").waitFor();
  assert.equal(await page.locator("[data-copy]").count(), 3);
  assert.equal(await page.locator("[data-copy] svg rect").count(), 3);

  await page.locator("[data-copy=main] svg rect").click();
  await page.waitForFunction(() => document.querySelector("[data-copy=main]").classList.contains("copied"));
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), mainName);
  assert.equal(await page.locator("input[type=checkbox]:checked").count(), 0);
  assert.equal(await page.locator("#bar").isVisible(), false);
  await page.evaluate(() => refresh());
  assert.equal(await page.locator("[data-copy=main]").getAttribute("title"), "已复制");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.key), "main:copy");
  await page.waitForFunction(() => !document.querySelector("[data-copy=main]").classList.contains("copied"));

  await page.locator(".name[for=select-main]").click();
  assert.equal(await page.locator("#select-main").isChecked(), true);
  assert.equal(await page.locator("#select-child").isDisabled(), true);
  await page.locator("[data-copy=child]").focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("[data-copy=child]").classList.contains("copied"));
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), childName);
  assert.equal(await page.locator("#select-main").isChecked(), true);
  assert.equal(await page.locator("#select-child").isChecked(), true);

  await page.locator("[data-copy=stray]").click();
  await page.waitForFunction(() => document.querySelector("[data-copy=stray]").classList.contains("copied"));
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "孤立子 kernel");
  await page.locator("#clear").click();
  assert.equal(await page.locator("input[type=checkbox]:checked").count(), 0);
  assert.equal(await page.locator("#bar").isVisible(), false);

  await page.evaluate(() => {
    Object.defineProperty(navigator.clipboard, "writeText", {
      configurable: true,
      value: async () => { throw new DOMException("Denied", "NotAllowedError"); },
    });
  });
  await page.locator("[data-copy=main]").click();
  await page.waitForFunction(() => document.querySelector("#listMessage").textContent.includes("复制失败"));
  assert.equal(await page.locator("[data-copy=main]").getAttribute("title"), "复制 kernel 名字");
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { value: undefined }));
  await page.locator("[data-copy=child]").click();
  await page.waitForFunction(() => !document.querySelector("[data-copy=child]").classList.contains("copied"));
  assert.match(await page.locator("#listMessage").innerText(), /复制失败/);

  await page.setViewportSize({ width: 390, height: 844 });
  const box = await page.locator("[data-copy=child]").boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 390);
  assert.equal(await page.locator("[data-copy=child]").isVisible(), true);
  assert.deepEqual(mutations, []);
  assert.deepEqual(errors, []);
  console.log("PASS: exact main/child/stray names, SVG and keyboard clicks, selection isolation, refresh feedback/focus, feedback expiry, denied/missing clipboard, mobile long names; no mutations or page errors.");
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
