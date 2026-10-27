/**
 * @file Parse maintained JavaScript and enforce JSDoc contracts without running it.
 */
import { parse } from "acorn";

/**
 * Visit syntax nodes while retaining the parent that gives anonymous helpers a name.
 * @param {object} node - Acorn syntax node to inspect.
 * @param {Function} visit - Callback receiving each node and its parent.
 * @param {object|null} parent - Enclosing syntax node, if any.
 * @returns {void} Nothing; the callback receives each syntax node once.
 */
export function walk(node, visit, parent = null) {
  visit(node, parent);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) {
      for (const child of value) if (child?.type) walk(child, visit, node);
    } else if (value?.type) walk(value, visit, node);
  }
}

/**
 * Resolve declarations and assigned helpers; inline callbacks belong to their operation.
 * @param {object} node - Function or class syntax node.
 * @param {object|null} parent - Syntax node containing the declaration or assignment.
 * @returns {string|null} Stable helper name, or null for an inline callback.
 */
export function symbolName(node, parent) {
  if (node.type === "FunctionDeclaration" || node.type === "ClassDeclaration") return node.id?.name;
  if (!["FunctionExpression", "ArrowFunctionExpression"].includes(node.type)) return null;
  if (parent?.type === "VariableDeclarator") return parent.id.name;
  if (["MethodDefinition", "Property"].includes(parent?.type)) return parent.key.name || parent.key.value;
  if (parent?.type === "AssignmentExpression") return parent.left.property?.name || parent.left.name;
  return node.id?.name || null;
}

/**
 * Find the immediately preceding JSDoc, not an unrelated file header or body comment.
 * @param {string} source - Complete JavaScript module text.
 * @param {object[]} comments - Acorn comments collected while parsing.
 * @param {object} node - Documented function or class syntax node.
 * @param {object|null} parent - Parent providing a variable or method declaration prefix.
 * @returns {string} Matching JSDoc text, or an empty string when no block is attached.
 */
export function leadingDocumentation(source, comments, node, parent) {
  const start = declarationStart(source, node, parent);
  const comment = comments.filter((item) => item.end <= start).at(-1);
  if (!comment || comment.type !== "Block" || !comment.value.startsWith("*")) return "";
  const between = source.slice(comment.end, start);
  if (!/^[\s]*(?:(?:export|default|const|let|var)\s+)*$/.test(between)) return "";
  return comment.value.includes("@file") ? "" : comment.value;
}

/**
 * Include export and variable keywords when locating an attached documentation block.
 * @param {string} source - Complete JavaScript source text.
 * @param {object} node - Named function or class syntax node.
 * @param {object|null} parent - Parent providing a variable or method prefix.
 * @returns {number} Source offset immediately after any leading indentation.
 */
export function declarationStart(source, node, parent) {
  const start = ["VariableDeclarator", "MethodDefinition", "Property", "AssignmentExpression"].includes(parent?.type) ? parent.start : node.start;
  const lineStart = source.lastIndexOf("\n", start - 1) + 1;
  const prefix = source.slice(lineStart, start);
  const match = prefix.match(/^(\s*)(?:(?:export|default|const|let|var)\s+)+$/);
  return match ? lineStart + match[1].length : start;
}

/**
 * Inspect one function body without counting nested callables as its implementation.
 * @param {object} node - Function syntax node.
 * @returns {object[]} Owned syntax nodes, excluding nested callable bodies.
 */
export function ownedNodes(node) {
  const result = [];
  const pending = [node.body];
  while (pending.length) {
    const current = pending.pop();
    result.push(current);
    if (current !== node.body && /Function|Class/.test(current.type)) continue;
    for (const value of Object.values(current)) {
      if (Array.isArray(value)) pending.push(...value.filter((child) => child?.type));
      else if (value?.type) pending.push(value);
    }
  }
  return result;
}

/**
 * Read a plain, defaulted, or rest parameter’s externally documented name.
 * @param {object} parameter - Acorn function-parameter syntax node.
 * @returns {string} Parameter name; destructuring must use an explicit named input helper.
 */
export function parameterName(parameter) {
  return parameter.name || parameter.left?.name || parameter.argument?.name || "<destructured>";
}

/**
 * Require typed parameter/result contracts and focused named functions.
 * @param {object} node - Function syntax node being audited.
 * @param {string} documentation - Attached JSDoc text, if present.
 * @param {string} name - Display name used for constructor handling.
 * @returns {string[]} Concrete diagnostics; empty when the function is fully documented.
 */
export function functionIssues(node, documentation, name) {
  const issues = [];
  const documented = [...documentation.matchAll(/@param\s+\{[^}]+\}\s+(?:\[)?(?:\.\.\.)?([\w$]+)(?:=[^\]]+)?\]?\s+-\s+\S.+/g)].map((match) => match[1]);
  const parameters = node.params.map(parameterName);
  for (const parameter of parameters) if (!documented.includes(parameter)) issues.push(`Document argument ${parameter} with a typed @param and explanation.`);
  for (const parameter of documented) if (!parameters.includes(parameter)) issues.push(`Remove stale @param ${parameter}.`);
  const owned = ownedNodes(node);
  const bindings = [...parameters, ...owned.filter((item) => item.type === "VariableDeclarator" && item.id.type === "Identifier").map((item) => item.id.name)];
  for (const binding of new Set(bindings)) if (binding.length === 1 && binding !== "_") issues.push(`Use a descriptive name instead of single-letter binding ${binding}.`);
  const returnsValue = node.body.type !== "BlockStatement" || owned.some((item) => item.type === "ReturnStatement" && item.argument);
  if (returnsValue && name !== "constructor" && !/@returns?\s+\{[^}]+\}\s+\S.+/.test(documentation)) issues.push("Explain the returned value with typed @returns.");
  if (owned.filter((item) => /Statement$|Declaration$/.test(item.type) && item.type !== "BlockStatement").length > 40) issues.push("Extract focused helpers; function exceeds 40 executable statements.");
  return issues;
}

/**
 * Audit a JavaScript file using its real syntax rather than text-pattern guesses.
 * @param {string} source - Complete maintained JavaScript or ES module text.
 * @param {string} path - Source label included in actionable diagnostics.
 * @returns {object} Contract count and source-located documentation failures.
 */
export function javascriptAudit(source, path = "<source>") {
  const comments = [];
  const tree = parse(source, { ecmaVersion: "latest", sourceType: "module", locations: true, onComment: comments });
  const issues = [];
  let symbols = 1;
  if (!comments.some((comment) => comment.start < tree.body[0]?.start && /@file\s+\S.+/.test(comment.value))) issues.push({ path, line: 1, symbol: "<module>", reason: "Add a concrete @file JSDoc header." });
  walk(tree, (node, parent) => {
    const name = symbolName(node, parent);
    if (!name) return;
    symbols += 1;
    const documentation = leadingDocumentation(source, comments, node, parent);
    const summary = documentation.replace(/^\*\s*/, "").split("\n")[0].trim();
    const reasons = [];
    if (summary.split(/\s+/).length < 3 || /\b(TODO|FIXME|TBD)\b/.test(summary)) reasons.push("Add a concrete class/callable JSDoc summary.");
    if (node.params) reasons.push(...functionIssues(node, documentation, name));
    if (node.type === "ClassDeclaration") {
      const properties = new Set();
      walk(node, (child) => {
        if (child.type === "AssignmentExpression" && child.left.object?.type === "ThisExpression") properties.add(child.left.property.name);
        if (child.type === "PropertyDefinition") properties.add(child.key.name);
      });
      for (const property of properties) if (!new RegExp(`@property\\s+\\{[^}]+\\}\\s+${property}\\s+-\\s+\\S.+`).test(documentation)) reasons.push(`Document attribute ${property} with typed @property.`);
    }
    for (const reason of reasons) issues.push({ path, line: node.loc.start.line, symbol: name, reason });
  });
  return { symbols, issues };
}
