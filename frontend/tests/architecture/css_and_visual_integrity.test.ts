import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC_ROOT = path.resolve(__dirname, "../../src");
const GLOBALS_CSS_PATH = path.resolve(SRC_ROOT, "app/globals.css");
const NEXT_CSS_DIR = path.resolve(__dirname, "../../.next/static/css");

function collectTsxFiles(dir: string): readonly string[] {
  const results: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...collectTsxFiles(full));
    } else if (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) {
      results.push(full);
    }
  }
  return results;
}

function extractCssSelectors(cssText: string): ReadonlySet<string> {
  const selectors = new Set<string>();
  const re = /\.([a-zA-Z_-][a-zA-Z0-9_-]*)/g;
  let match: RegExpExecArray | null = re.exec(cssText);
  while (match !== null) {
    if (match[1]) {
      selectors.add(match[1]);
    }
    match = re.exec(cssText);
  }
  return selectors;
}

function extractClassTokensFromNode(expr: ts.Node, out: Set<string>): void {
  if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr)) {
    for (const tok of expr.text.trim().split(/\s+/)) {
      if (tok.length > 0) {
        out.add(tok);
      }
    }
    return;
  }
  if (ts.isTemplateExpression(expr)) {
    for (const tok of expr.head.text.trim().split(/\s+/)) {
      if (tok.length > 0 && !expr.head.rawText?.endsWith("-")) {
        out.add(tok);
      }
    }
    for (const span of expr.templateSpans) {
      extractClassTokensFromNode(span.expression, out);
      if (!span.literal.text.startsWith("-")) {
        for (const tok of span.literal.text.trim().split(/\s+/)) {
          if (tok.length > 0) {
            out.add(tok);
          }
        }
      }
    }
    return;
  }
  if (ts.isConditionalExpression(expr)) {
    extractClassTokensFromNode(expr.whenTrue, out);
    extractClassTokensFromNode(expr.whenFalse, out);
    return;
  }
  if (ts.isBinaryExpression(expr)) {
    extractClassTokensFromNode(expr.left, out);
    extractClassTokensFromNode(expr.right, out);
  }
}

function collectUsedClassNames(sf: ts.SourceFile): ReadonlySet<string> {
  const tokens = new Set<string>();
  const visit = (node: ts.Node): void => {
    if (
      ts.isJsxAttribute(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === "className" &&
      node.initializer !== undefined
    ) {
      if (ts.isStringLiteral(node.initializer)) {
        extractClassTokensFromNode(node.initializer, tokens);
      } else if (ts.isJsxExpression(node.initializer) && node.initializer.expression) {
        extractClassTokensFromNode(node.initializer.expression, tokens);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return tokens;
}

function getJsxAttributeClassString(opening: ts.JsxOpeningLikeElement): string {
  for (const attr of opening.attributes.properties) {
    if (
      ts.isJsxAttribute(attr) &&
      ts.isIdentifier(attr.name) &&
      attr.name.text === "className" &&
      attr.initializer !== undefined
    ) {
      if (ts.isStringLiteral(attr.initializer)) {
        return attr.initializer.text;
      }
      if (ts.isJsxExpression(attr.initializer) && attr.initializer.expression) {
        const tmp = new Set<string>();
        extractClassTokensFromNode(attr.initializer.expression, tmp);
        return Array.from(tmp).join(" ");
      }
    }
  }
  return "";
}

describe("Frontend CSS Compilation & Visual Spacing Integrity", () => {
  const files = collectTsxFiles(SRC_ROOT);
  const globalsCss = fs.readFileSync(GLOBALS_CSS_PATH, "utf8");
  const definedSelectors = extractCssSelectors(globalsCss);

  it("defines every semantic className used across all TSX files in globals.css", () => {
    const missing: string[] = [];

    for (const file of files) {
      if (!file.endsWith(".tsx")) {
        continue;
      }
      const sf = ts.createSourceFile(
        file,
        fs.readFileSync(file, "utf8"),
        ts.ScriptTarget.Latest,
        true,
      );
      const used = collectUsedClassNames(sf);
      for (const token of used) {
        if (!definedSelectors.has(token)) {
          missing.push(`${path.relative(SRC_ROOT, file)}: .${token}`);
        }
      }
    }

    expect(missing).toEqual([]);
  });

  it("defines flex/grid spacing containers for all multi-badge and multi-pill JSX groups", () => {
    const allowedFlexContainers = new Set([
      "nav-inner",
      "nav-brand-group",
      "nav-brand-link",
      "nav-tabs",
      "nav-status-pill",
      "cmd-status-bar",
      "cmd-status-pills",
      "cmd-portal-grid",
      "cmd-pill",
      "persona-switch",
      "cmd-portal-cta",
      "cmd-candidate-top",
      "cmd-candidate-bottom",
      "badge-row",
      "meta-strip",
      "button-row",
      "checkbox-chip-row",
      "page-title-row",
      "ledger-meta",
      "telemetry-row",
      "composer-footer",
      "toggle-card",
      "toggle-control",
    ]);

    // Verify each separating container actually declares display flex/grid + gap or justify-content in CSS
    for (const containerClass of allowedFlexContainers) {
      const rulePattern = new RegExp(
        `(?:\\.[a-zA-Z0-9_-]+\\s*,\\s*)*\\.${containerClass}(?:\\s*,\\s*\\.[a-zA-Z0-9_-]+)*\\s*\\{[^}]*\\}`,
        "s",
      );
      const ruleMatch = globalsCss.match(rulePattern);
      expect(ruleMatch, `Missing CSS rule for .${containerClass}`).not.toBeNull();
      const ruleBody = ruleMatch?.[0] ?? "";
      expect(
        ruleBody.includes("display: flex") ||
          ruleBody.includes("display: inline-flex") ||
          ruleBody.includes("display: grid"),
        `.${containerClass} must use flex/grid layout`,
      ).toBe(true);
      expect(
        ruleBody.includes("gap:") || ruleBody.includes("justify-content: space-between"),
        `.${containerClass} must declare explicit gap or space-between`,
      ).toBe(true);
    }

    // Walk every JSX element with >=2 direct <span> or <a> children and verify container class exists
    const unspacedGroups: string[] = [];
    for (const file of files) {
      if (!file.endsWith(".tsx")) {
        continue;
      }
      const sf = ts.createSourceFile(
        file,
        fs.readFileSync(file, "utf8"),
        ts.ScriptTarget.Latest,
        true,
      );
      const visit = (node: ts.Node): void => {
        if (ts.isJsxElement(node)) {
          const inlineChildren = node.children.filter((child) => {
            if (!ts.isJsxElement(child)) {
              return false;
            }
            const tag = child.openingElement.tagName.getText(sf);
            return tag === "span" || tag === "Link";
          });
          if (inlineChildren.length >= 2) {
            const cls = getJsxAttributeClassString(node.openingElement);
            const hasAllowed = cls
              .split(/\s+/)
              .some((tok) => allowedFlexContainers.has(tok));
            if (!hasAllowed) {
              unspacedGroups.push(
                `${path.relative(SRC_ROOT, file)}: <${node.openingElement.tagName.getText(sf)} className="${cls}"> has ${inlineChildren.length} inline children without flex-gap container`,
              );
            }
          }
        }
        ts.forEachChild(node, visit);
      };
      visit(sf);
    }

    expect(unspacedGroups).toEqual([]);
  });

  it("verifies compiled Next.js CSS bundle (when present) includes core layout, grid, and badge selectors", () => {
    if (!fs.existsSync(NEXT_CSS_DIR)) {
      return;
    }
    const cssFiles = fs.readdirSync(NEXT_CSS_DIR).filter((f) => f.endsWith(".css"));
    expect(cssFiles.length).toBeGreaterThan(0);
    const bundleContent = cssFiles
      .map((f) => fs.readFileSync(path.join(NEXT_CSS_DIR, f), "utf8"))
      .join("\n");

    const criticalSelectors = [
      "nav-header",
      "nav-tabs",
      "cmd-hero-panel",
      "cmd-kpi-grid",
      "cmd-portal-grid",
      "cmd-pipeline-grid",
      "cmd-candidate-card",
      "interview-split-grid",
      "progress-track",
      "data-table",
      "badge-success",
      "badge-info",
      "btn-primary",
    ];
    for (const sel of criticalSelectors) {
      expect(bundleContent.includes(`.${sel}`), `Missing .${sel} in compiled bundle`).toBe(true);
    }
    expect(bundleContent.length).toBeGreaterThan(5000);
  });
});
