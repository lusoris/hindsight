import type { Root } from "postcss";
import { beforeAll, expect, it } from "vitest";
import { compileGlobals } from "./compile-globals";

let compiled: Root;

beforeAll(async () => {
  compiled = await compileGlobals();
});

it("generates paragraph, heading and list styles for rendered Markdown", () => {
  // Check the compiled output: registering typography only in an unloaded
  // Tailwind v3 config leaves the prose class names present but inert in v4.
  const declarations = new Map<string, Set<string>>();
  compiled.walkRules((rule) => {
    for (const element of ["p", "h2", "ol", "ul"]) {
      if (!rule.selector.includes(`:where(${element})`)) continue;
      rule.walkDecls((declaration) => {
        const properties = declarations.get(element) ?? new Set<string>();
        properties.add(declaration.prop);
        declarations.set(element, properties);
      });
    }
  });
  expect(declarations.get("p")).toContain("margin-top");
  expect(declarations.get("h2")).toContain("font-size");
  expect(declarations.get("ol")).toContain("list-style-type");
  expect(declarations.get("ul")).toContain("list-style-type");
});

it("wraps long answer text without reducing table cells' intrinsic minimum widths", () => {
  // Unlike `overflow-wrap: anywhere`, break-word leaves min-content sizing intact. Chromium
  // verifies that a six-column table scrolls while a long paragraph URL wraps.
  const wrapping: string[] = [];
  compiled.walkRules(".reflect-answer", (rule) => {
    rule.walkDecls("overflow-wrap", (declaration) => {
      wrapping.push(declaration.value);
    });
  });
  expect(wrapping).toEqual(["break-word"]);
});
