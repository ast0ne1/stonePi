// @ts-check
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: ".",
  timeout: 45000,
  retries: 0,
  use: {
    headless: true,
    ignoreHTTPSErrors: true,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
