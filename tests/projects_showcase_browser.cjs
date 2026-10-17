const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "@playwright/test");

const base = process.env.PROJECTS_BASE_URL || "http://127.0.0.1:8796";
const projectUrls = [
  "https://therealestatedeallab.com/",
  "https://greenfoodsmarket.com/",
  "https://www.youtube.com/@layer8cultureradio",
  "https://sonar.layer8culture.io/",
];

async function waitForProjects(page) {
  await page.locator("#projects").waitFor({ state: "visible" });
  await page.locator('body > nav a[data-projects-nav][href="/projects/"]').waitFor({ state: "attached" });
}

async function readHeaderAppearance(page) {
  return page.locator("body > nav").evaluate(nav => {
    const style = getComputedStyle(nav);
    return {
      transparent: nav.classList.contains("bg-transparent"),
      scrolledBackground: nav.classList.contains("bg-ink-bg/70"),
      scrolledBlur: nav.classList.contains("backdrop-blur-2xl"),
      position: style.position,
      height: nav.getBoundingClientRect().height,
    };
  });
}

async function assertStaticHeaderMatches(page, expected, route) {
  await page.waitForTimeout(650);
  const atTop = await readHeaderAppearance(page);
  assert.deepEqual(atTop, expected.top, `${route} header matches homepage at top`);
  await page.mouse.wheel(0, 420);
  await page.waitForTimeout(650);
  const scrolled = await readHeaderAppearance(page);
  assert.deepEqual(scrolled, expected.scrolled, `${route} header matches homepage when scrolled`);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(650);
  assert.deepEqual(await readHeaderAppearance(page), expected.top,
    `${route} header returns to the homepage's transparent state`);
}

async function waitForProjectAnchor(page) {
  await page.waitForFunction(() => {
    const section = document.querySelector("#projects");
    const header = document.querySelector("body > nav");
    return section && header &&
      section.getBoundingClientRect().top >= header.getBoundingClientRect().bottom - 2 &&
      section.getBoundingClientRect().top < header.getBoundingClientRect().bottom + 40;
  });
}

async function assertRadarSweep(page, width) {
  let previousTransform;
  for (let frame = 0; frame < 3; frame += 1) {
    await page.waitForTimeout(750);
    const sample = await page.locator(".l8-projects__radar-sweep").evaluate(sweep => {
      const svg = sweep.ownerSVGElement;
      const bounds = sweep.getBoundingClientRect();
      const viewport = svg.getBoundingClientRect();
      const center = new DOMPoint(350, 177).matrixTransform(sweep.getScreenCTM());
      const expectedCenter = new DOMPoint(350, 177).matrixTransform(svg.getScreenCTM());
      return {
        transform: getComputedStyle(sweep).transform,
        transformBox: getComputedStyle(sweep).transformBox,
        centerDelta: Math.hypot(center.x - expectedCenter.x, center.y - expectedCenter.y),
        contained: bounds.left >= viewport.left - 1 &&
          bounds.top >= viewport.top - 1 &&
          bounds.right <= viewport.right + 1 &&
          bounds.bottom <= viewport.bottom + 1,
      };
    });
    assert.equal(sample.transformBox, "view-box", `radar pivot uses the SVG viewBox at ${width}px`);
    assert.ok(sample.centerDelta <= 1, `radar pivot remains centered at ${width}px: ${sample.centerDelta}px`);
    assert.equal(sample.contained, true, `radar sweep stays inside the SVG at ${width}px`);
    if (previousTransform !== undefined) {
      assert.notEqual(sample.transform, previousTransform, `radar sweep rotates over time at ${width}px`);
    }
    previousTransform = sample.transform;
  }
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      page.on("console", message => {
        if (message.type() === "error") errors.push(`console: ${message.text()}`);
      });
      page.on("response", response => {
        if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
      });
      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(1100);
      assert.equal(await page.locator("#projects").count(), 0, "Projects are not on the homepage");
      assert.equal(await page.locator("#build").evaluate(node =>
        node.nextElementSibling?.id === "stream"
      ), true, "homepage Build flows directly into Stream");
      assert.equal(await page.locator('body > nav a[data-projects-nav][href="/projects/"]').count(), 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true,
        `homepage overflows at ${width}px`);

      await page.goto(`${base}/projects/`, { waitUntil: "networkidle" });
      await waitForProjects(page);

      assert.equal(await page.locator("#projects article").count(), 4);
      assert.equal(await page.locator("#projects h1").textContent(), "REAL PEOPLE.REAL BUILDS.");
      assert.equal(await page.locator("body > nav").count(), 1);
      assert.equal(await page.locator("footer").count(), 1);
      assert.equal(await page.title(), "Projects | Layer8.Culture");
      assert.equal(await page.locator('link[rel="canonical"]').getAttribute("href"),
        "https://www.layer8culture.io/projects/");
      assert.equal(await page.locator('body > nav a[data-projects-nav][aria-current="page"]').count(), 1);
      if (width === 1440) {
        await page.reload({ waitUntil: "networkidle" });
        await waitForProjects(page);
        assert.equal(await page.locator("#projects article").count(), 4);
      }

      const links = await page.locator("#projects article a[target=_blank]").evaluateAll(nodes =>
        nodes.map(node => ({
          href: node.href,
          rel: node.rel,
          label: node.getAttribute("aria-label"),
        }))
      );
      assert.deepEqual(links.map(link => link.href), projectUrls);
      for (const link of links) {
        assert.match(link.rel, /noopener/);
        assert.match(link.rel, /noreferrer/);
        assert.match(link.label, /opens in a new tab/);
      }

      await page.locator("#projects").scrollIntoViewIfNeeded();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true,
        `homepage overflows at ${width}px`);
      assert.equal(await page.locator("#projects").evaluate(node => node.scrollWidth <= node.clientWidth + 1), true,
        `Projects section overflows at ${width}px`);

      const radio = page.locator("[data-radio-image]");
      await radio.scrollIntoViewIfNeeded();
      await page.waitForFunction(() =>
        document.querySelector("[data-radio-image]")?.src.endsWith("/projects/assets/radio-loop.gif")
      );
      await page.locator("[data-motion-toggle]").click();
      await page.waitForFunction(() =>
        document.querySelector("[data-radio-image]")?.src.endsWith("/projects/assets/radio-poster.jpg")
      );
      assert.equal(await page.locator("#projects").getAttribute("data-motion"), "paused");
      await page.locator("[data-motion-toggle]").click();
      await radio.scrollIntoViewIfNeeded();
      await page.waitForFunction(() =>
        document.querySelector("[data-radio-image]")?.src.endsWith("/projects/assets/radio-loop.gif")
      );
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForFunction(() =>
        document.querySelector("[data-radio-image]")?.src.endsWith("/projects/assets/radio-poster.jpg")
      );

      await page.locator("[data-sonar-card]").scrollIntoViewIfNeeded();
      await page.waitForFunction(() =>
        getComputedStyle(document.querySelector(".l8-projects__radar-sweep")).animationName === "l8-radar-sweep"
      );
      await assertRadarSweep(page, width);
      if (width < 768) {
        await page.locator('body > nav button[aria-expanded]').click();
        const menu = page.locator('[role="dialog"]');
        await menu.locator('a[data-projects-nav][href="/projects/"]').waitFor();
        assert.equal(await menu.locator('a[data-projects-nav]').count(), 1);
        await menu.locator('a[data-projects-nav]').click();
        await page.waitForFunction(() => {
          const dialog = document.querySelector('[role="dialog"]');
          return !dialog || !dialog.open;
        });
        await waitForProjects(page);
      }

      assert.deepEqual(errors, [], `browser errors at ${width}px`);
      console.log(`PASS ${width}px homepage, nav, card links, motion, and responsive overflow`);
      await page.close();
    }

    const homeMobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const homeMobileErrors = [];
    homeMobile.on("pageerror", error => homeMobileErrors.push(error.message));
    homeMobile.on("console", message => {
      if (message.type() === "error") homeMobileErrors.push(`console: ${message.text()}`);
    });
    await homeMobile.goto(base, { waitUntil: "networkidle" });
    await homeMobile.locator('body > nav a[data-projects-nav][href="/projects/"]')
      .waitFor({ state: "attached" });
    await homeMobile.locator('body > nav button[aria-expanded]').click();
    const homeMenu = homeMobile.locator('[role="dialog"]');
    await homeMenu.locator('a[data-projects-nav][href="/projects/"]').waitFor();
    assert.equal(await homeMenu.locator('a[data-projects-nav]').count(), 1);
    await homeMenu.locator('a[data-projects-nav]').click();
    await homeMobile.waitForURL("**/projects/");
    await waitForProjects(homeMobile);
    assert.deepEqual(homeMobileErrors, []);
    await homeMobile.close();
    console.log("PASS homepage mobile navigation opens the standalone Projects page without hydration errors");

    for (const width of [1440, 390]) {
      const headerPage = await browser.newPage({ viewport: { width, height: 900 } });
      await headerPage.goto(base, { waitUntil: "networkidle" });
      await headerPage.waitForTimeout(1100);
      const expected = { top: await readHeaderAppearance(headerPage) };
      await headerPage.mouse.wheel(0, 420);
      await headerPage.waitForTimeout(650);
      expected.scrolled = await readHeaderAppearance(headerPage);
      assert.equal(expected.top.transparent, true);
      assert.equal(expected.top.scrolledBackground, false);
      assert.equal(expected.scrolled.scrolledBackground, true);
      assert.equal(expected.scrolled.scrolledBlur, true);
      await headerPage.evaluate(() => window.scrollTo(0, 0));
      await headerPage.waitForTimeout(650);

      for (const route of [
        "/guides/",
        "/guides/ai-builder-workflow-map/",
        "/guides/mcp-explained-without-the-hype/",
        "/guides/build-your-ai-org-chart/",
        "/guides/seven-prompting-habits-that-make-ai-useful/",
        "/guides/ai-fluency-consumer-user-builder-operator/",
        "/projects/",
      ]) {
        await headerPage.goto(`${base}${route}`, { waitUntil: "networkidle" });
        await assertStaticHeaderMatches(headerPage, expected, `${route} (${width}px)`);
      }
      await headerPage.close();
      console.log(`PASS matching homepage header styles across guides and Projects at ${width}px`);
    }

    const reduced = await browser.newPage({ viewport: { width: 390, height: 900 } });
    const reducedErrors = [];
    reduced.on("pageerror", error => reducedErrors.push(error.message));
    reduced.on("console", message => {
      if (message.type() === "error") reducedErrors.push(`console: ${message.text()}`);
    });
    await reduced.emulateMedia({ reducedMotion: "reduce" });
    await reduced.goto(`${base}/projects/`, { waitUntil: "networkidle" });
    await waitForProjects(reduced);
    await reduced.locator("[data-radio-card]").scrollIntoViewIfNeeded();
    await reduced.locator("[data-sonar-card]").scrollIntoViewIfNeeded();
    assert.equal(await reduced.locator("#projects").getAttribute("data-motion"), "paused");
    assert.equal(await reduced.locator("[data-motion-toggle]").isDisabled(), true);
    assert.ok((await reduced.locator("[data-radio-image]").getAttribute("src")).endsWith("radio-poster.jpg"));
    assert.equal(await reduced.locator(".l8-projects__radar-sweep").evaluate(node =>
      getComputedStyle(node).animationName
    ), "none");
    assert.deepEqual(reducedErrors, []);
    await reduced.close();
    console.log("PASS reduced-motion poster and stopped Sonar radar");

    const legacy = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const legacyErrors = [];
    legacy.on("pageerror", error => legacyErrors.push(error.message));
    legacy.on("console", message => {
      if (message.type() === "error") legacyErrors.push(`console: ${message.text()}`);
    });
    await legacy.goto(`${base}/#projects`, { waitUntil: "networkidle" });
    await legacy.waitForURL("**/projects/");
    await waitForProjects(legacy);
    assert.deepEqual(legacyErrors, []);
    await legacy.close();
    console.log("PASS legacy /#projects links redirect to the standalone page");

    const guide = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const guideErrors = [];
    guide.on("pageerror", error => guideErrors.push(error.message));
    guide.on("console", message => {
      if (message.type() === "error") guideErrors.push(`console: ${message.text()}`);
    });
    await guide.goto(`${base}/guides/ai-builder-workflow-map/`, { waitUntil: "networkidle" });
    await guide.locator("h1").waitFor();
    await guide.locator('body > nav button[aria-expanded]').click();
    const guideMenu = guide.locator('dialog[aria-label="Main navigation"]');
    await guideMenu.locator('a[data-projects-nav][href="/projects/"]').waitFor();
    assert.equal(await guideMenu.locator('a[data-projects-nav]').count(), 1);
    await guideMenu.locator('a[data-projects-nav]').click();
    await guide.waitForURL("**/projects/");
    await waitForProjects(guide);
    assert.equal(await guide.locator("#projects article").count(), 4);
    assert.deepEqual(guideErrors, []);
    await guide.close();
    console.log("PASS guide-to-home Projects deep link and preserved guide rendering");
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
