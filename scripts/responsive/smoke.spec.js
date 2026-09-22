// Optional overflow smoke — see README.md
const { test, expect } = require("@playwright/test");

const BASE = process.env.STONEPI_BASE_URL || "http://127.0.0.1:8000";

const VIEWPORTS = [
  { name: "iphone-13-mini", width: 375, height: 812 },
  { name: "tablet-portrait", width: 800, height: 1280 },
  { name: "tablet-landscape", width: 1280, height: 800 },
  { name: "laptop-14-720", width: 1280, height: 720 },
  { name: "laptop-14-1200", width: 1920, height: 1200 },
  { name: "desktop-1080", width: 1920, height: 1080 },
];

const ROUTES = JSON.parse(
  process.env.STONEPI_ROUTES ||
    JSON.stringify([
      { name: "auth-login", path: "/auth/login" },
      { name: "portal-home", path: "/" },
      { name: "portal-health", path: "/overview" },
      { name: "newscast", path: "/news/" },
      { name: "eventtrakr", path: "/events/" },
      { name: "fileserve", path: "/files/" },
      { name: "sportguide", path: "/sports/" },
      { name: "pricescout", path: "/prices/" },
      { name: "studio", path: "/studio/" },
      { name: "pinboard", path: "/pinboard/" },
    ])
);

for (const vp of VIEWPORTS) {
  for (const route of ROUTES) {
    test(`${route.name} @ ${vp.name} no horizontal overflow`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`${BASE}${route.path}`, { waitUntil: "domcontentloaded", timeout: 30000 });
      const overflow = await page.evaluate(() => {
        const doc = document.documentElement;
        return {
          scrollWidth: doc.scrollWidth,
          clientWidth: doc.clientWidth,
          innerWidth: window.innerWidth,
        };
      });
      expect(
        overflow.scrollWidth,
        `${route.path} scrollWidth ${overflow.scrollWidth} > innerWidth ${overflow.innerWidth}`
      ).toBeLessThanOrEqual(overflow.innerWidth + 1);
    });
  }
}
