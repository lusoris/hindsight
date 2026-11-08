import { readFileSync, writeFileSync } from "node:fs";

/**
 * Drop the README's H1 heading and any H2 sections (including their subheadings)
 * named in `names`.
 *
 * @param {string} markdown - Source markdown content.
 * @param {string[]} names - Titles of the H2 sections to drop.
 * @returns {string} Markdown text with H1 and specified H2 sections removed.
 */
export function dropSections(markdown, names) {
  const lines = markdown.split("\n");
  const out = [];
  let dropping = false;
  for (const line of lines) {
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const [, hashes, text] = heading;
      if (hashes.length === 1) continue; // the H1 becomes frontmatter `title`
      // Only an H2 opens or closes a dropped section. Recomputing on every heading would let an
      // H3 *inside* a dropped section switch dropping back off and leak the rest of it.
      if (hashes.length === 2) {
        dropping = names.includes(text.trim());
        if (dropping) continue;
      } else if (dropping) {
        continue;
      }
    }
    if (!dropping) out.push(line);
  }
  return out.join("\n");
}

/**
 * Clean up markdown text for docs site rendering: drop repo-relative links, rewrite absolute
 * domain URLs to site-relative paths, collapse excess blank lines, and trim whitespace.
 *
 * @param {string} text - Markdown text to transform.
 * @returns {string} Cleaned markdown text.
 */
export function siteBody(text) {
  return (
    text
      // Repo-relative links 404 on the docs site; keep the label, drop the link.
      .replace(/\[([^\]]+)\]\((?!https?:|\/)[^)]+\)/g, "$1")
      // Our own absolute URLs -> site-relative. Assets so the page uses THIS build's static
      // files rather than whatever is live in production; doc links so they resolve within the
      // site (and so the docs-skill generator can turn them into file-relative paths, which it
      // cannot do with an absolute URL). The README keeps them absolute because it also renders
      // on GitHub, where site-relative would 404.
      .replace(/https:\/\/hindsight\.vectorize\.io\/([^\s"')]+)/g, "/$1")
      .replace(/\n{3,}/g, "\n\n")
      .trim()
  );
}

/**
 * Write the generated docs page or verify that it matches the current file on disk.
 *
 * @param {object} options
 * @param {string} options.label - Integration label (e.g. 'coding-agents', 'hermes').
 * @param {string} options.page - Target documentation page file path.
 * @param {string} options.generated - Generated page content.
 * @param {string} options.script - Repo-relative script path to run if out of date.
 */
export function writeOrCheck({ label, page, generated, script }) {
  if (process.argv.includes("--check")) {
    const current = readFileSync(page, "utf8");
    if (current !== generated) {
      console.error(
        `[${label}] ❌ docs page is out of date with the README.\n  Run: node ${script}`
      );
      process.exit(1);
    }
    console.log(`[${label}] ✅ docs page matches the README.`);
  } else {
    writeFileSync(page, generated);
    console.log(`[${label}] wrote ${page} from the README.`);
  }
}
