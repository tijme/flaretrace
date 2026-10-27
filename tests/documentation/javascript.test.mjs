/** @file Prove JSDoc checks reject incomplete contracts without confusing code-like strings or callbacks. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { javascriptAudit } from "./javascript.mjs";

const header = "/** @file Normalize administrator labels without executing markup. */\n";

for (const [name, source, expected] of [
  ["file header", "const label = 1;", "@file"],
  ["function summary", header + "function missing() {}", "summary"],
  ["argument", header + "/** Trim one supplied label. */\nfunction label(value) {}", "argument value"],
  ["return", header + "/** Read a fixed label. */\nconst label = () => 'ready';", "returned"],
  ["stale argument", header + "/** Set a fixed label.\n * @param {string} oldName - Removed input.\n */\nfunction label() {}", "stale"],
  ["class attribute", header + "/** Hold one supplied label. */\nclass Label { /** Initialize label state. */ constructor() { this.value = 'ready'; } }", "attribute value"],
  ["unattached comment", header + "/** Set a fixed label. */\nconst unrelated = 1; function label() {}", "summary"],
  ["placeholder", header + "/** TODO describe this later. */\nfunction label() {}", "summary"],
  ["readable name", header + "/** Set a fixed label. */\nfunction label() { const x = 1; }", "single-letter"],
  ["oversized function", header + "/** Perform too many operations. */\nfunction oversized() {" + "doWork();".repeat(41) + "}", "40 executable"],
]) {
  test(`JSDoc rejects missing ${name}`, () => {
    assert.ok(javascriptAudit(source).issues.some((issue) => issue.reason.includes(expected)));
  });
}

test("JSDoc accepts exported helpers, defaults, rest parameters, and typed results", () => {
  const source = header + `/** Join supplied label fragments.
 * @param {string} prefix - Label prefix.
 * @param {...string} fragments - Remaining label pieces.
 * @returns {string} Joined label text.
 */
export function label(prefix = '', ...fragments) { return prefix + fragments.join(''); }`;
  assert.deepEqual(javascriptAudit(source).issues, []);
});

test("JSDoc ignores code-like strings and attributes inline callbacks to their operation", () => {
  const source = header + `const sample = 'function fake(value) { return value; }';
/** Register a deferred callback. */
function register() { setTimeout(() => 1, 0); }`;
  assert.deepEqual(javascriptAudit(source).issues, []);
});

test("JSDoc documents instance attributes and constructor inputs independently", () => {
  const source = header + `/** Hold one supplied label.
 * @property {string} value - Current label text.
 */
class Label {
/** Initialize one label instance.
 * @param {string} value - Initial label text.
 */
constructor(value) { this.value = value; }
}`;
  assert.deepEqual(javascriptAudit(source).issues, []);
});
