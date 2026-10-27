/** @file Audit maintained JavaScript files and emit machine-readable CI documentation results. */
import { readdirSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { javascriptAudit } from "./javascript.mjs";

/**
 * Discover production scripts and test/checker modules without scanning dependencies.
 * @param {string} root - Absolute repository root.
 * @returns {string[]} Sorted absolute paths to maintained JavaScript sources.
 */
export function javascriptFiles(root) {
  return ["src/flaretrace/static", "tests/javascript", "tests/documentation"].flatMap((directory) =>
    readdirSync(resolve(root, directory)).filter((name) => /\.m?js$/.test(name)).map((name) => resolve(root, directory, name)),
  ).sort();
}

/**
 * Require complete JSDoc coverage and persist actionable diagnostics for CI artifacts.
 * @returns {void} Nothing; documentation failures set the process exit status to one.
 */
export function main() {
  const root = resolve(fileURLToPath(new URL("../..", import.meta.url)));
  const files = javascriptFiles(root);
  const results = files.map((path) => javascriptAudit(readFileSync(path, "utf8"), relative(root, path)));
  const issues = results.flatMap((result) => result.issues);
  const symbols = results.reduce((total, result) => total + result.symbols, 0);
  const output = resolve(root, "artifacts/ci/documentation");
  mkdirSync(output, { recursive: true });
  writeFileSync(resolve(output, "javascript.json"), JSON.stringify({ files: files.length, symbols, complete: issues.length === 0, issues }, null, 2) + "\n");
  for (const issue of issues) console.error(`${issue.path}:${issue.line}: ${issue.symbol}: ${issue.reason}`);
  console.log(`JavaScript documentation: ${files.length} files, ${symbols} symbols, ${issues.length} issues.`);
  if (issues.length) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main();
