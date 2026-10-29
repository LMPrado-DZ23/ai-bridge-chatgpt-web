const { test, expect } = require("bun:test");
const fs = require("node:fs");
const path = require("node:path");

const mainSource = fs.readFileSync(
  path.join(__dirname, "..", "electron", "main.cjs"),
  "utf8",
);

test("Bridge shutdown kills the Windows PyInstaller process tree to prevent orphan services", () => {
  expect(mainSource).toContain('spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"]');
  expect(mainSource).toContain('app.on("will-quit", () => {');
  expect(mainSource).toContain('terminateBridgeService(bridgeServiceChild);');
});

test("successful packaged smoke launch bypasses the normal quit interception after cleanup", () => {
  const markerWrite = mainSource.indexOf('runtimeVerified: true');
  const exitCommit = mainSource.indexOf("exitCommitted = true;", markerWrite);
  const quitCall = mainSource.indexOf("app.quit();", exitCommit);
  expect(markerWrite).toBeGreaterThanOrEqual(0);
  expect(exitCommit).toBeGreaterThan(markerWrite);
  expect(quitCall).toBeGreaterThan(exitCommit);
});

test("Windows packaged smoke round-trips the AI Bridge workspace through authenticated RPC", () => {
  const smokeStart = mainSource.indexOf('if (launcherSmokeTest) {');
  const markerWrite = mainSource.indexOf('bridgeIntegrationVerified,', smokeStart);
  expect(smokeStart).toBeGreaterThanOrEqual(0);
  expect(mainSource).toContain('const { spawn, spawnSync } = require("node:child_process");');
  expect(mainSource.indexOf('await setBridgeUiActive(true);', smokeStart)).toBeGreaterThan(smokeStart);
  expect(mainSource.indexOf('window.pywebview.api.state()', smokeStart)).toBeGreaterThan(smokeStart);
  expect(mainSource.indexOf('window.pywebview.api.show_codex_workspace()', smokeStart)).toBeGreaterThan(smokeStart);
  expect(mainSource.indexOf('candidate.textContent?.trim() === "AI Bridge"', smokeStart)).toBeGreaterThan(smokeStart);
  expect(markerWrite).toBeGreaterThan(smokeStart);
});

test("failed packaged smoke launch exits with a failure code instead of hanging", () => {
  const smokeFailure = mainSource.indexOf('if (process.argv.includes("--launcher-smoke-test")) {', mainSource.indexOf("void start().catch"));
  const failureExit = mainSource.indexOf("app.exit(1);", smokeFailure);
  expect(smokeFailure).toBeGreaterThanOrEqual(0);
  expect(failureExit).toBeGreaterThan(smokeFailure);
});
