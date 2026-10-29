import { readdirSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";

const [bunMajor, bunMinor, bunPatch] = Bun.version.split(".").map(Number);
if (bunMajor < 1 || (bunMajor === 1 && bunMinor < 4) || (bunMajor === 1 && bunMinor === 4 && bunPatch < 4)) {
  throw new Error(
    `The isolated test runner requires Bun >=1.4.4; detected ${Bun.version}. Use a separate Bun installation instead of replacing the system runtime.`,
  );
}

const repositoryRoot = process.cwd();
const testRoot = resolve(repositoryRoot, "tests");
const testFilePattern = /\.(?:test|spec)\.(?:ts|tsx|js|cjs|mjs)$/;

function collectTestFiles(directory: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const fullPath = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...collectTestFiles(fullPath));
    else if (entry.isFile() && testFilePattern.test(entry.name)) files.push(fullPath);
  }
  return files;
}

const testFiles = collectTestFiles(testRoot).sort();
if (testFiles.length === 0) {
  throw new Error(`No test files found under ${testRoot}`);
}

const failures: Array<{ file: string; status: number | null; error?: string }> = [];
for (let index = 0; index < testFiles.length; index += 1) {
  const file = testFiles[index];
  process.stdout.write(`\n[${index + 1}/${testFiles.length}] ${relative(repositoryRoot, file)}\n`);
  const result = spawnSync(process.execPath, ["test", file], {
    cwd: repositoryRoot,
    env: process.env,
    stdio: "inherit",
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    failures.push({
      file: relative(repositoryRoot, file),
      status: result.status,
      ...(result.error ? { error: result.error.message } : {}),
    });
  }
}

if (failures.length > 0) {
  process.stderr.write(`\n${failures.length} test file(s) failed:\n`);
  for (const failure of failures) {
    process.stderr.write(`- ${failure.file}: ${failure.error || `exit ${failure.status}`}\n`);
  }
  process.exitCode = 1;
} else {
  process.stdout.write(`\nAll ${testFiles.length} test files passed in isolated Bun processes.\n`);
}
