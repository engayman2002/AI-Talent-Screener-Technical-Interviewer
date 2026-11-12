import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC_ROOT = path.resolve(__dirname, "../../src");

function collectSourceFiles(dir: string): readonly string[] {
  const results: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...collectSourceFiles(full));
    } else if (
      (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) &&
      !entry.name.endsWith(".d.ts")
    ) {
      results.push(full);
    }
  }
  return results;
}

function resolveLayer(filePath: string): string {
  const rel = path.relative(SRC_ROOT, filePath).split(path.sep);
  return rel[0] ?? "unknown";
}

function extractModuleSpecifiers(sf: ts.SourceFile): readonly string[] {
  const specs: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier !== undefined &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      specs.push(node.moduleSpecifier.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return specs;
}

function findAstViolations(sf: ts.SourceFile): {
  readonly hasAnyKeyword: boolean;
  readonly identifierRefs: ReadonlySet<string>;
  readonly newConstructors: ReadonlySet<string>;
  readonly directFetchCall: boolean;
} {
  let hasAnyKeyword = false;
  let directFetchCall = false;
  const identifierRefs = new Set<string>();
  const newConstructors = new Set<string>();

  const visit = (node: ts.Node): void => {
    if (node.kind === ts.SyntaxKind.AnyKeyword) {
      hasAnyKeyword = true;
    }
    if (ts.isIdentifier(node)) {
      identifierRefs.add(node.text);
    }
    if (ts.isNewExpression(node) && ts.isIdentifier(node.expression)) {
      newConstructors.add(node.expression.text);
    }
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "fetch"
    ) {
      directFetchCall = true;
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);

  return { hasAnyKeyword, identifierRefs, newConstructors, directFetchCall };
}

function resolvesIntoLayer(fromFile: string, specifier: string, targetLayer: string): boolean {
  if (!specifier.startsWith(".") && !specifier.startsWith("@/")) {
    return false;
  }
  const resolved = specifier.startsWith("@/")
    ? path.resolve(SRC_ROOT, specifier.slice(2))
    : path.resolve(path.dirname(fromFile), specifier);
  const rel = path.relative(SRC_ROOT, resolved);
  return rel.split(path.sep)[0] === targetLayer;
}

describe("ADR-010 Frontend Clean Architecture Dependency Rules", () => {
  const files = collectSourceFiles(SRC_ROOT);

  it("discovers files across all 4 concentric layers", () => {
    const layers = new Set(files.map(resolveLayer));
    expect(layers.has("domain")).toBe(true);
    expect(layers.has("application")).toBe(true);
    expect(layers.has("infrastructure")).toBe(true);
    expect(layers.has("presentation")).toBe(true);
    expect(layers.has("app")).toBe(true);
  });

  it("enforces zero explicit `any` keywords across src/", () => {
    const offenders: string[] = [];
    for (const file of files) {
      const code = fs.readFileSync(file, "utf8");
      const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
      const sf = ts.createSourceFile(file, code, ts.ScriptTarget.ES2022, true, kind);
      if (findAstViolations(sf).hasAnyKeyword) {
        offenders.push(path.relative(SRC_ROOT, file));
      }
    }
    expect(offenders).toEqual([]);
  });

  it("keeps domain/ 100% pure (zero React/Next, outer layers, or browser globals)", () => {
    const forbiddenPackages = new Set(["react", "react-dom", "next"]);
    const forbiddenGlobals = ["window", "document", "AudioContext", "WebSocket", "EventSource", "fetch"];
    const outerLayers = ["application", "infrastructure", "presentation", "app", "composition"];

    for (const file of files.filter((f) => resolveLayer(f) === "domain")) {
      const code = fs.readFileSync(file, "utf8");
      const sf = ts.createSourceFile(file, code, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TS);
      const specs = extractModuleSpecifiers(sf);
      for (const spec of specs) {
        expect(forbiddenPackages.has(spec.split("/")[0] ?? spec)).toBe(false);
        for (const outer of outerLayers) {
          expect(resolvesIntoLayer(file, spec, outer)).toBe(false);
        }
      }
      const ast = findAstViolations(sf);
      for (const g of forbiddenGlobals) {
        expect(ast.identifierRefs.has(g), `${file} referenced ${g}`).toBe(false);
      }
    }
  });

  it("keeps application/ isolated from infrastructure/, presentation/, app/, React, and Next", () => {
    const outerOrUiLayers = ["infrastructure", "presentation", "app", "composition"];
    const forbiddenGlobals = ["window", "document", "AudioContext", "WebSocket", "EventSource", "fetch"];

    for (const file of files.filter((f) => resolveLayer(f) === "application")) {
      const code = fs.readFileSync(file, "utf8");
      const sf = ts.createSourceFile(file, code, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TS);
      const specs = extractModuleSpecifiers(sf);
      for (const spec of specs) {
        const pkgRoot = spec.split("/")[0] ?? spec;
        expect(["react", "react-dom", "next"].includes(pkgRoot)).toBe(false);
        for (const forbidden of outerOrUiLayers) {
          expect(resolvesIntoLayer(file, spec, forbidden)).toBe(false);
        }
      }
      const ast = findAstViolations(sf);
      for (const g of forbiddenGlobals) {
        expect(ast.identifierRefs.has(g), `${file} referenced ${g}`).toBe(false);
      }
    }
  });

  it("prevents presentation/ and app/ from importing infrastructure/ or raw network/audio instantiations", () => {
    for (const file of files.filter((f) => {
      const l = resolveLayer(f);
      return l === "presentation" || l === "app";
    })) {
      const code = fs.readFileSync(file, "utf8");
      const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
      const sf = ts.createSourceFile(file, code, ts.ScriptTarget.ES2022, true, kind);
      const specs = extractModuleSpecifiers(sf);
      for (const spec of specs) {
        expect(resolvesIntoLayer(file, spec, "infrastructure"), `${file} -> ${spec}`).toBe(false);
      }
      const ast = findAstViolations(sf);
      expect(ast.newConstructors.has("WebSocket"), `${file} new WebSocket`).toBe(false);
      expect(ast.newConstructors.has("AudioContext"), `${file} new AudioContext`).toBe(false);
      expect(ast.newConstructors.has("EventSource"), `${file} new EventSource`).toBe(false);
      expect(ast.directFetchCall, `${file} direct fetch()`).toBe(false);
    }
  });
});
