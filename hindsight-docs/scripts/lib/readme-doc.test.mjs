// Unit tests for the README -> docs page helpers. Run by the docs `build` script before the two
// `sync-*-doc.mjs --check` steps, which cover each generator's own output against its page.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, it, mock } from "node:test";

import { dropSections, siteBody, writeOrCheck } from "./readme-doc.mjs";

describe("dropSections", () => {
  const readme = [
    "# Title",
    "intro",
    "## Keep",
    "kept",
    "## Development",
    "dropped",
    "### Nested",
    "also dropped",
    "## After",
    "### Sub",
    "after",
  ].join("\n");

  it("drops the H1 and a named H2 with its subsections, keeping later sections", () => {
    assert.equal(
      dropSections(readme, ["Development"]),
      ["intro", "## Keep", "kept", "## After", "### Sub", "after"].join("\n")
    );
  });

  it("keeps every H2 when no names are given", () => {
    assert.equal(dropSections(readme, []), readme.split("\n").slice(1).join("\n"));
  });
});

describe("siteBody", () => {
  it("drops repo-relative links and keeps absolute and site-relative ones", () => {
    assert.equal(
      siteBody("[a](../x.md) [b](https://example.com) [c](/docs/y)"),
      "a [b](https://example.com) [c](/docs/y)"
    );
  });

  it("rewrites own absolute URLs to site-relative paths", () => {
    assert.equal(
      siteBody(
        "![x](https://hindsight.vectorize.io/img/a.png) https://hindsight.vectorize.io/docs/b"
      ),
      "![x](/img/a.png) /docs/b"
    );
  });

  it("collapses three or more newlines and trims", () => {
    assert.equal(siteBody("\n\na\n\n\n\nb\n\n"), "a\n\nb");
  });
});

describe("writeOrCheck", () => {
  let dir;
  let page;
  let argv;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "readme-doc-"));
    page = join(dir, "page.md");
    argv = process.argv;
    mock.method(console, "log", () => {});
    mock.method(console, "error", () => {});
  });

  afterEach(() => {
    process.argv = argv;
    mock.restoreAll();
    rmSync(dir, { recursive: true, force: true });
  });

  const options = () => ({ label: "x", page, generated: "new\n", script: "scripts/x.mjs" });

  it("writes the page without --check", () => {
    process.argv = ["node", "script"];
    writeOrCheck(options());
    assert.equal(readFileSync(page, "utf8"), "new\n");
  });

  it("with --check leaves a matching page alone and does not exit", () => {
    process.argv = ["node", "script", "--check"];
    writeFileSync(page, "new\n");
    const exit = mock.method(process, "exit", () => {});
    writeOrCheck(options());
    assert.equal(exit.mock.callCount(), 0);
  });

  it("with --check exits 1 on a mismatch and names the script, without writing", () => {
    process.argv = ["node", "script", "--check"];
    writeFileSync(page, "old\n");
    const exit = mock.method(process, "exit", () => {});
    writeOrCheck(options());
    assert.deepEqual(exit.mock.calls[0].arguments, [1]);
    assert.match(console.error.mock.calls[0].arguments[0], /Run: node scripts\/x\.mjs/);
    assert.equal(readFileSync(page, "utf8"), "old\n");
  });
});
