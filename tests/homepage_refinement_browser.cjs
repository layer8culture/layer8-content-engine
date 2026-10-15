const assert = require("node:assert/strict");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "@playwright/test");

const base = process.env.HOMEPAGE_BASE_URL || "http://127.0.0.1:8796";
const guideRoutes = [
  "/guides/",
  "/guides/ai-builder-workflow-map/",
  "/guides/mcp-explained-without-the-hype/",
  "/guides/build-your-ai-org-chart/",
  "/guides/seven-prompting-habits-that-make-ai-useful/",
  "/guides/ai-fluency-consumer-user-builder-operator/",
];
const originalHeights = { 1440: 8525, 390: 10124 };

async function waitForStyles(page, href) {
  await page.waitForFunction(expected =>
    Array.from(document.styleSheets).some(sheet => sheet.href?.endsWith(expected)),
  href);
}

async function assertNoOverflow(page, label) {
  const width = await page.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  assert.ok(width.document <= width.viewport + 1, `${label} horizontal overflow: ${JSON.stringify(width)}`);
}

async function assertNoClippedText(page, label) {
  const clipped = await page.locator("main h1, main h2, main h3, main p").evaluateAll(nodes =>
    nodes.filter(node => {
      if (node.clientHeight < 8) return false;
      const style = getComputedStyle(node);
      return (["hidden", "clip"].includes(style.overflowY) &&
          node.scrollHeight > node.clientHeight + 4) ||
        (["hidden", "clip"].includes(style.overflowX) &&
          node.scrollWidth > node.clientWidth + 8);
    }).map(node => node.textContent.trim().replace(/\s+/g, " ").slice(0, 80))
  );
  assert.deepEqual(clipped, [], `${label} clips text`);
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: width < 500 ? 844 : 900 } });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      page.on("console", message => {
        if (message.type() === "error") errors.push(`console: ${message.text()}`);
      });
      page.on("response", response => {
        if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
      });

      await page.goto(base, { waitUntil: "networkidle" });
      await waitForStyles(page, "/homepage-refinement.css");
      await page.waitForFunction(() =>
        document.querySelector("#top p")?.textContent.includes("Learn AI by building")
      );
      await page.waitForFunction(() =>
        Array.from(document.querySelectorAll("#stream a"))
          .some(link => link.textContent.trim() === "Watch on YouTube")
      );
      const home = await page.evaluate(() => ({
        height: document.documentElement.scrollHeight,
        heroSize: getComputedStyle(document.querySelector("#top .hero-display-2col")).fontSize,
        heroImageHeight: document.querySelector('#top div:has(> img[src="/hero/builder-studio.png"])')
          .getBoundingClientRect().height,
        bodyFont: getComputedStyle(document.querySelector("#about p.text-base")).fontSize,
        buildPadding: getComputedStyle(document.querySelector("#build")).paddingTop,
      }));
      assert.equal(home.heroSize, width === 1440 ? "64px" : width < 360 ? "32px" : "36px");
      assert.ok(parseFloat(home.bodyFont) >= 16 && parseFloat(home.bodyFont) <= 18,
        `homepage body text remains readable: ${home.bodyFont}`);
      assert.equal(home.buildPadding, width < 768 ? "56px" : "72px");
      if (width < 768) assert.equal(home.heroImageHeight, 300);
      if (originalHeights[width]) {
        assert.ok(home.height < originalHeights[width] * 0.8,
          `homepage at ${width}px should be materially shorter: ${home.height}px`);
      }
      assert.equal(await page.locator("#projects").count(), 0);
      assert.equal(await page.locator("#build").evaluate(node => node.nextElementSibling?.id), "stream");
      assert.equal(await page.locator('#top a[href="/guides/"]').textContent().then(text => text.trim()),
        "Explore the guides");
      assert.equal(await page.locator('#stream a[href*="youtube.com"]').textContent().then(text => text.trim()),
        "Watch on YouTube");
      assert.equal(await page.locator("#stream a").filter({ hasText: "Get show updates" }).count(), 1);
      assert.equal(await page.getByText("Design preview only", { exact: false }).count(), 0);
      await assertNoOverflow(page, `homepage ${width}px`);
      await assertNoClippedText(page, `homepage ${width}px`);

      const email = page.locator('#join input[type="email"]');
      assert.equal(await email.count(), 1, "production signup email field remains present");
      await email.fill("not-an-email");
      assert.equal(await email.evaluate(input => input.validity.typeMismatch), true);
      await email.fill("builder@example.com");
      assert.equal(await email.evaluate(input => input.validity.valid), true,
        "production signup retains native email validation");

      if (width < 768) {
        await page.locator('body > nav button[aria-expanded]').click();
        const menu = page.locator('[role="dialog"]');
        await menu.locator('a[data-projects-nav][href="/projects/"]').waitFor();
        await menu.locator('a[data-projects-nav]').focus();
        await page.keyboard.press("Enter");
        await page.waitForURL("**/projects/");
        assert.equal(await page.locator("#projects article").count(), 4);
      } else {
        await page.locator('body > nav a[data-projects-nav][href="/projects/"]').focus();
        await page.keyboard.press("Enter");
        await page.waitForURL("**/projects/");
        assert.equal(await page.locator("#projects article").count(), 4);
      }
      await page.close();
      console.log(`PASS homepage ${width}px: concise content, actual destinations, signup, keyboard navigation, no overflow; height ${home.height}px`);
      assert.deepEqual(errors, [], `browser errors at ${width}px`);
    }

    for (const width of [1440, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: width < 500 ? 844 : 900 } });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      page.on("console", message => {
        if (message.type() === "error") errors.push(`console: ${message.text()}`);
      });
      page.on("response", response => {
        if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
      });
      for (const route of [...guideRoutes, "/projects/"]) {
        await page.goto(`${base}${route}`, { waitUntil: "networkidle" });
        await waitForStyles(page, "/static-page-rhythm.css");
        const heading = await page.locator("main h1").first().evaluate(node => ({
          size: getComputedStyle(node).fontSize,
          text: node.textContent.trim(),
        }));
        if (route === "/projects/") {
          assert.equal(heading.size, width === 1440 ? "44px" : width === 390 ? "32px" : "32px");
          assert.equal(await page.locator("#projects article").count(), 4);
          assert.equal(await page.locator('body > nav a[data-projects-nav][aria-current="page"]').count(), 1);
        } else {
          assert.equal(heading.size, width === 1440 ? "44px" : "32px", `${route} heading size`);
        }
        const bodyText = page.locator("main p").first();
        assert.ok(parseFloat(await bodyText.evaluate(node => getComputedStyle(node).fontSize)) >= 16,
          `${route} body text remains readable`);
        await assertNoOverflow(page, `${route} ${width}px`);
        await assertNoClippedText(page, `${route} ${width}px`);
      }
      assert.deepEqual(errors, [], `guide and Projects errors at ${width}px`);
      await page.close();
      console.log(`PASS guide library, all five guide pages, and Projects rhythm at ${width}px`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
