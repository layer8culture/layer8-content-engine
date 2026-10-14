const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "@playwright/test");
const base = process.env.GUIDE_BASE_URL || "http://127.0.0.1:8795";
const slugs = [
  "ai-builder-workflow-map",
  "mcp-explained-without-the-hype",
  "build-your-ai-org-chart",
  "seven-prompting-habits-that-make-ai-useful",
  "ai-fluency-consumer-user-builder-operator",
];

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 } });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      page.on("response", response => {
        if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
      });
      for (const slug of slugs) {
        await page.goto(`${base}/guides/${slug}/`, { waitUntil: "networkidle" });
        assert.equal(await page.locator("h1").count(), 1);
        assert.equal(await page.locator(".fg-panel:visible").count(), 1);
        const cards = page.locator(".fg-card");
        await cards.last().click();
        assert.match(await page.locator(".fg-position").textContent(), /^\d+ \/ \d+$/);
        assert.equal(await page.locator("[data-next]").isDisabled(), true);
        await page.locator("[data-previous]").click();
        assert.equal(await page.locator("[data-next]").isDisabled(), false);
        await page.locator(".fg-deeper:visible").click();
        assert.ok(await page.locator(".fg-reference[open]").count() > 0);
        await page.locator(".fg-reference").last().locator("summary").click();
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
        assert.equal(overflow, false, `${slug}: overflow at ${width}px`);
        await page.locator(".fg-motion").click();
        assert.equal(await page.locator(".fg").getAttribute("data-motion"), "off");
        await page.emulateMedia({ reducedMotion: "reduce" });
        await page.waitForFunction(() => document.querySelector(".fg-motion").disabled);
        assert.equal(await page.locator(".fg-motion").isDisabled(), true);
        assert.equal(await page.locator(".fg").getAttribute("data-motion"), "off");
        await page.emulateMedia({ reducedMotion: "no-preference" });
        await page.waitForFunction(() => !document.querySelector(".fg-motion").disabled);
        if (width < 768) {
          await page.locator('body > nav button[aria-expanded]').click();
          assert.equal(await page.locator("dialog[open] a").count(), 5);
          await page.keyboard.press("Escape");
          await page.waitForFunction(() =>
            document.querySelector('body > nav button[aria-expanded]').getAttribute("aria-expanded") === "false");
          assert.equal(await page.locator("dialog[open]").count(), 0);
          assert.equal(await page.locator('body > nav button[aria-expanded]').getAttribute("aria-expanded"), "false");
        }
        await page.goto(`${base}/guides/${slug}/#topic-2`, { waitUntil: "networkidle" });
        await page.locator("#topic-2").waitFor({ state: "visible" });
        assert.equal(await page.locator("#topic-2").isVisible(), true);
        await page.locator(".fg-card").first().focus();
        await page.keyboard.press("Enter");
        assert.equal(await page.locator("#topic-1").isVisible(), true);
        assert.equal(await page.locator("#topic-1 h2").evaluate(node => node === document.activeElement), true);
        if (process.env.GUIDE_SCREENSHOTS && width !== 320) {
          await page.goto(`${base}/guides/${slug}/`, { waitUntil: "networkidle" });
          await page.screenshot({
            path: path.join(process.env.GUIDE_SCREENSHOTS, `${slug}-${width}.png`),
            fullPage: true,
          });
        }
        console.log(`PASS ${width}px ${slug}`);
      }
      assert.deepEqual(errors, [], `Browser errors at ${width}px`);
      await page.close();
    }
    const regression = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await regression.goto(base, { waitUntil: "networkidle" });
    await regression.locator('body > nav a[href="/guides/"]').click();
    await regression.waitForURL("**/guides/");
    await regression.locator('h2 a[href="/guides/build-your-ai-org-chart/"]').click();
    await regression.waitForURL("**/guides/build-your-ai-org-chart/");
    assert.equal(await regression.locator("[data-visual-guide]").count(), 1);
    for (const route of ["/", "/terms/", "/privacy/"]) {
      await regression.goto(`${base}${route}`, { waitUntil: "networkidle" });
      assert.equal(await regression.locator("body > nav").count(), 1);
      assert.equal(await regression.locator("footer").count(), 1);
      assert.equal(await regression.locator(".fg").count(), 0);
    }
    await regression.close();
    console.log("PASS guide-library navigation and unchanged non-guide shell");
    const noJS = await browser.newPage({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
    for (const slug of slugs) {
      await noJS.goto(`${base}/guides/${slug}/`);
      assert.ok(await noJS.locator(".fg-panel:visible").count() >= 4);
      await noJS.locator(".fg-reference summary").first().click();
      assert.equal(await noJS.locator(".fg-original").first().isVisible(), true);
    }
    await noJS.close();
    console.log("PASS no-JavaScript progressive disclosure");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
