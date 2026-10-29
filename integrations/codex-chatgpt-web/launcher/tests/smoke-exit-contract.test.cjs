const { test, expect } = require("bun:test");
const fs = require("node:fs");
const path = require("node:path");

const mainSource = fs.readFileSync(
  path.join(__dirname, "..", "electron", "main.cjs"),
  "utf8",
);

test("successful packaged smoke launch bypasses the normal quit interception after cleanup", () => {
  const markerWrite = mainSource.indexOf('runtimeVerified: true');
  const exitCommit = mainSource.indexOf("exitCommitted = true;", markerWrite);
  const quitCall = mainSource.indexOf("app.quit();", exitCommit);
  expect(markerWrite).toBeGreaterThanOrEqual(0);
  expect(exitCommit).toBeGreaterThan(markerWrite);
  expect(quitCall).toBeGreaterThan(exitCommit);
});

test("failed packaged smoke launch exits with a failure code instead of hanging", () => {
  const smokeFailure = mainSource.indexOf('if (process.argv.includes("--launcher-smoke-test")) {', mainSource.indexOf("void start().catch"));
  const failureExit = mainSource.indexOf("app.exit(1);", smokeFailure);
  expect(smokeFailure).toBeGreaterThanOrEqual(0);
  expect(failureExit).toBeGreaterThan(smokeFailure);
});
