const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const { validateRuntimeBundle } = require("../electron/runtime-install.cjs");

const launcherRoot = path.resolve(__dirname, "..");
const artifactsDirectory = path.join(launcherRoot, "artifacts");
const launcherManifest = JSON.parse(
  fs.readFileSync(path.join(launcherRoot, "package.json"), "utf8"),
);
const expectedVersion = launcherManifest.version;
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "codex-web-gpt-package-smoke-"));
const markerPath = path.join(scratch, "ready.json");
const coreHome = path.join(scratch, "core-home");
const isolatedInstall = path.join(scratch, "install");
const registryBackup = path.join(scratch, "launcher-registry-before.reg");
const launcherRegistryKey = `HKCU\\Software\\${launcherManifest.build.nsis.guid}`;
const registryBefore = spawnSync("reg.exe", ["export", launcherRegistryKey, registryBackup, "/y"], {
  encoding: "utf8",
  windowsHide: true,
});
const hadRegistryBefore = registryBefore.status === 0 && fs.existsSync(registryBackup);
const shortcutRoots = [
  path.join(process.env.APPDATA || "", "Microsoft", "Windows", "Start Menu", "Programs"),
  path.join(process.env.ProgramData || "C:\\ProgramData", "Microsoft", "Windows", "Start Menu", "Programs"),
  path.join(process.env.USERPROFILE || os.homedir(), "Desktop"),
];
const existingShortcuts = new Map();
function captureShortcuts(root) {
  if (!fs.existsSync(root)) return;
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const fullPath = path.join(root, entry.name);
    if (entry.isDirectory()) captureShortcuts(fullPath);
    else if (/codex web gpt/i.test(entry.name)) existingShortcuts.set(fullPath, fs.readFileSync(fullPath));
  }
}
for (const root of shortcutRoots) captureShortcuts(root);
let macAppBundle;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || scratch,
    env: options.env || process.env,
    encoding: options.stdio === "ignore" ? undefined : "utf8",
    stdio: options.stdio || "pipe",
    maxBuffer: 8 * 1024 * 1024,
    timeout: options.timeout || 45_000,
    windowsHide: true,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `${command} failed with status ${result.status}: ${result.stderr?.trim() || result.stdout?.trim() || "no output"}`,
    );
  }
}

function windowsInstallLocation() {
  const guid = launcherManifest.build.nsis.guid;
  const registryKey = `HKCU\\Software\\${guid}`;
  const result = spawnSync("reg.exe", ["query", registryKey, "/v", "InstallLocation"], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`Windows installer did not register ${registryKey}: ${result.stderr?.trim() || "no output"}`);
  }
  const match = result.stdout.match(/^\s*InstallLocation\s+REG_SZ\s+(.+?)\s*$/mi);
  if (!match || !path.win32.isAbsolute(match[1])) {
    throw new Error(`Windows installer registered an invalid InstallLocation: ${result.stdout.trim()}`);
  }
  return match[1];
}

function artifact(pattern, label) {
  const matches = fs.readdirSync(artifactsDirectory)
    .filter((name) => pattern.test(name))
    .sort();
  if (matches.length !== 1) {
    throw new Error(`Expected exactly one ${label} in ${artifactsDirectory}; found ${matches.join(", ") || "none"}`);
  }
  return path.join(artifactsDirectory, matches[0]);
}

function startIsolatedLauncherWatchdog(executable) {
  const escapedPath = executable.replaceAll("'", "''");
  const script = `$target='${escapedPath}'; $deadline=(Get-Date).AddSeconds(115); while((Get-Date) -lt $deadline) { $p=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $target }; if($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; break }; Start-Sleep -Milliseconds 200 }`;
  const watchdog = spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    stdio: "ignore",
    windowsHide: true,
    detached: true,
  });
  watchdog.unref();
  return watchdog;
}

function smokeEnvironment() {
  const launcherData = path.join(scratch, "launcher-data");
  if (process.platform === "win32") {
    // The bridge round-trip smoke exercises the workspace navigation from the normal shell.
    // Seed only this disposable profile as already configured so the real workspace sidebar is
    // visible; production state and the user's profile are never touched.
    fs.mkdirSync(launcherData, { recursive: true });
    fs.writeFileSync(path.join(launcherData, "launcher-state.json"), `${JSON.stringify({
      version: 1,
      language: "en",
      onboardingComplete: true,
      coreSetupComplete: true,
      codexCatalogVerified: true,
      mcpSetupComplete: false,
      mcpRuntimeInstalled: false,
      codexRestartRequired: false,
      autoStart: false,
      keepRunningOnClose: false,
      browserInteractionMode: "manual",
      sidebarOpen: true,
      sidebarWidth: 252,
    }, null, 2)}\n`);
  }
  return {
    ...process.env,
    TMPDIR: scratch,
    CODEX_WEB_GPT_LAUNCHER_DATA_DIR: launcherData,
    CODEX_CHATGPT_WEB_HOME: coreHome,
    CODEX_HOME: path.join(scratch, "codex-home"),
    CODEX_WEB_GPT_SMOKE_FILE: markerPath,
  };
}

try {
  let executable;
  let command;
  let args;
  const env = smokeEnvironment();

  if (process.platform === "darwin") {
    const archive = artifact(/-mac-(?:arm64|x64)\.zip$/, "macOS launcher archive");
    const stage = path.join(scratch, "stage");
    fs.mkdirSync(stage);
    run("ditto", ["-x", "-k", archive, stage]);
    macAppBundle = path.join(stage, "Codex Web GPT.app");
    executable = path.join(macAppBundle, "Contents", "MacOS", "Codex Web GPT");
    command = executable;
    args = ["--launcher-smoke-test"];
  } else if (process.platform === "linux") {
    if (!["x64", "arm64"].includes(process.arch)) {
      throw new Error(`Unsupported Linux AppImage architecture: ${process.arch}`);
    }
    executable = artifact(new RegExp(`-linux-${process.arch}\\.AppImage$`), `Linux ${process.arch} AppImage`);
    fs.chmodSync(executable, 0o755);
    run(path.join(launcherRoot, "scripts", "smoke-linux-appimage-symbols.sh"), [executable], {
      timeout: 120_000,
    });
    command = "xvfb-run";
    args = ["-a", executable, "--launcher-smoke-test"];
    env.APPIMAGE_EXTRACT_AND_RUN = "1";
  } else if (process.platform === "win32") {
    const installer = artifact(/-win-x64\.exe$/, "Windows installer");
    fs.mkdirSync(isolatedInstall, { recursive: true });
    // Install into the disposable smoke directory, never over the user's normal app path.
    // Some NSIS builds wait for the post-install app to exit. A narrowly scoped watchdog stops
    // only that temporary executable so the installer can return before the explicit smoke run.
    const isolatedExecutable = path.join(isolatedInstall, `${launcherManifest.build.productName}.exe`);
    const watchdog = startIsolatedLauncherWatchdog(isolatedExecutable);
    try {
      // This build is per-user (perMachine=false); /currentuser is only valid for NSIS multi-user installers.
      run(installer, ["/S", `/D=${isolatedInstall}`], {
        timeout: 120_000,
        env,
      });
    } finally {
      watchdog.kill();
    }
    executable = path.join(windowsInstallLocation(), `${launcherManifest.build.productName}.exe`);
    if (!path.resolve(executable).toLowerCase().startsWith(path.resolve(isolatedInstall).toLowerCase())) {
      throw new Error(`Installer escaped the isolated smoke directory: ${executable}`);
    }
    command = executable;
    args = ["--launcher-smoke-test"];
  } else {
    throw new Error(`Unsupported package smoke platform: ${process.platform}`);
  }

  if (!fs.existsSync(executable)) throw new Error(`Packaged launcher executable is missing: ${executable}`);
  // First launch may hash the staged runtime while Windows Defender scans newly installed files.
  // Keep a bounded smoke timeout that accommodates that integrity check without hanging indefinitely.
  run(command, args, {
    env,
    timeout: process.platform === "win32" ? 240_000 : 45_000,
    // Electron may leave helper descendants holding inherited stdout/stderr handles after the
    // main process writes the marker and quits. Ignore stdio for this smoke launch to avoid
    // spawnSync waiting on those unrelated pipe handles.
    stdio: process.platform === "win32" ? "ignore" : undefined,
  });
  if (!fs.existsSync(markerPath)) throw new Error("Packaged launcher did not write its readiness marker");
  const marker = JSON.parse(fs.readFileSync(markerPath, "utf8"));
  if (marker.ok !== true
    || marker.packaged !== true
    || marker.runtimeVerified !== true
    || (process.platform === "win32" && marker.bridgeIntegrationVerified !== true)
    || marker.version !== expectedVersion
    || marker.platform !== process.platform) {
    throw new Error(`Unexpected packaged launcher marker: ${JSON.stringify(marker)}`);
  }
  const installedRuntime = path.join(
    coreHome,
    "versions",
    `${expectedVersion}-${process.platform}-${process.arch}`,
  );
  const installedManifest = JSON.parse(
    fs.readFileSync(path.join(installedRuntime, "manifest.json"), "utf8"),
  );
  validateRuntimeBundle(installedRuntime, {
    version: expectedVersion,
    platform: process.platform,
    arch: process.arch,
  });
  if (installedManifest.schemaVersion !== 2
    || installedManifest.appVersion !== expectedVersion
    || installedManifest.platform !== process.platform
    || installedManifest.arch !== process.arch
    || !Array.isArray(installedManifest.files)
    || installedManifest.files.length === 0
    || !/^[a-f0-9]{64}$/.test(installedManifest.bundleId)) {
    throw new Error(`Packaged launcher installed the wrong durable runtime: ${JSON.stringify(installedManifest)}`);
  }
  process.stdout.write(`PACKAGED_LAUNCHER_SMOKE_OK ${process.platform}/${process.arch}\n`);
} finally {
  try {
    if (macAppBundle) {
      const launchServices =
        "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister";
      run(
        launchServices,
        ["-u", macAppBundle],
      );
      run(launchServices, ["-gc"]);
    }
  } finally {
    if (process.platform === "win32") {
      // Restore the per-user installer registry exactly as it was before the smoke test.
      if (hadRegistryBefore) {
        const restored = spawnSync("reg.exe", ["import", registryBackup], { encoding: "utf8", windowsHide: true });
        if (restored.status !== 0) throw new Error(`Could not restore launcher registry backup: ${restored.stderr || restored.stdout}`);
      } else {
        spawnSync("reg.exe", ["delete", launcherRegistryKey, "/f"], { encoding: "utf8", windowsHide: true });
      }
      // Remove shortcuts created by the temporary install and restore any matching shortcuts
      // that existed before it. Do not touch unrelated shortcuts.
      for (const root of shortcutRoots) {
        if (!fs.existsSync(root)) continue;
        const walk = (directory) => {
          for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
            const fullPath = path.join(directory, entry.name);
            if (entry.isDirectory()) walk(fullPath);
            else if (/codex web gpt/i.test(entry.name) && !existingShortcuts.has(fullPath)) {
              fs.rmSync(fullPath, { force: true });
            }
          }
        };
        walk(root);
      }
      for (const [shortcutPath, contents] of existingShortcuts) {
        fs.mkdirSync(path.dirname(shortcutPath), { recursive: true });
        fs.writeFileSync(shortcutPath, contents);
      }
    }
    if (process.env.KEEP_SMOKE_ARTIFACTS === "1") {
      process.stderr.write(`SMOKE_ARTIFACTS_PRESERVED ${scratch}\\n`);
    } else {
      fs.rmSync(scratch, { recursive: true, force: true });
    }
  }
}
