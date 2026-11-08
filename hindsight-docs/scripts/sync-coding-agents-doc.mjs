#!/usr/bin/env node
/**
 * Generate docs-integrations/coding-agents.md from the integration's README.
 *
 * The two pages said the same things in different words and drifted — the docs page kept install
 * instructions that no longer worked and a harness table that had fallen behind. The README is the
 * single source now; this rewrites the doc page from it so "keep them in sync" is mechanical
 * instead of a habit.
 *
 * Two adjustments are applied, both because the audience differs:
 *   - Docusaurus frontmatter replaces the README's H1 (the title comes from frontmatter).
 *   - Repo-relative links (`src/…`, `../other-integration`) are dropped to plain text: they resolve
 *     on GitHub but 404 on the docs site.
 *   - Absolute asset URLs on our own domain become site-relative. The README must use absolute URLs
 *     so images render on npm and GitHub, but on the docs site those same URLs pin every image to
 *     PRODUCTION — so a new asset shows as broken locally and in previews until it is deployed,
 *     which is exactly when you are trying to look at it.
 *   - A `<!-- figure: <name> -->` line becomes a React component, imported at the top (see
 *     FIGURES). The README keeps an invisible comment there, since a React component renders on
 *     neither GitHub nor npm.
 *   - The `skill:begin` / `skill:end` markers are dropped. They tell the integration's
 *     scripts/build-skill.mjs which regions the companion skill copies; MDX has no HTML comments,
 *     so leaving them in would fail the docs build outright.
 *
 * Run: node hindsight-docs/scripts/sync-coding-agents-doc.mjs [--check]
 * `--check` fails when the doc page is out of date instead of writing it (for CI).
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { dropSections, siteBody, writeOrCheck } from './lib/readme-doc.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const readme = join(here, '..', '..', 'hindsight-integrations', 'coding-agents', 'README.md');
const page = join(here, '..', 'docs-integrations', 'coding-agents.md');

// Kept in the doc page's frontmatter rather than derived, so title/description stay tuned for
// search without touching the README's own heading.
const FRONTMATTER = `---
sidebar_position: 6
title: "Coding Agents"
description: "One Hindsight memory plugin for coding agents — per-repo memory banks built automatically from git history and past sessions, injected into the agent as it works."
---

{/* GENERATED from hindsight-integrations/coding-agents/README.md — edit that file, then run
    node hindsight-docs/scripts/sync-coding-agents-doc.mjs */}
`;

/**
 * `<!-- figure: NAME -->` in the README -> this component on the doc page, with its imports.
 * The marker is an HTML comment so it stays invisible on GitHub and npm, which render the README
 * but cannot render React.
 */
const FIGURES = {
  'coding-agents': {
    jsx: '<Figure doc={codingAgents} />',
    imports: [
      "import Figure from '@site/src/components/Figure';",
      "import codingAgents from '@site/figures/coding-agents.json';",
    ],
  },
  'coding-agents-benchmark': {
    jsx: "<CodingAgentsChart title=\"AMB · sdebench\" />",
    imports: ["import CodingAgentsChart from '@site/src/components/CodingAgentsChart';"],
  },
};

/** Sections that only make sense inside the repo (contributor-facing), dropped from the doc page. */
const DROP_SECTIONS = ['Layout', 'Ingestion internals (no CLI)', 'Companion skill (generated)'];

function build() {
  const src = readFileSync(readme, 'utf8');
  const body = siteBody(
    dropSections(src, DROP_SECTIONS)
      // Region markers for the companion-skill generator — invalid syntax in MDX, and meaningless
      // to a reader of the docs site either way.
      .replace(/^<!--\s*skill:(?:begin|end)[^>]*-->\n?/gm, '')
      .replace(/^<!--\s*figure: ([a-z0-9-]+)\s*-->$/gm, (line, name) => {
        // Loud, rather than leaving the comment in place: MDX has no HTML comments, so a typo'd
        // marker would otherwise surface as an unrelated parse error in the docs build.
        if (!FIGURES[name]) {
          throw new Error(
            `[coding-agents] unknown figure "${name}" in the README — add it to FIGURES or fix the marker.`,
          );
        }
        return FIGURES[name].jsx;
      }),
  );
  const imports = Object.values(FIGURES)
    .filter((f) => body.includes(f.jsx))
    .flatMap((f) => f.imports)
    .join('\n');
  return `${FRONTMATTER}\n${imports ? `${imports}\n\n` : ''}${body}\n`;
}

writeOrCheck({
  label: 'coding-agents',
  page,
  generated: build(),
  script: 'hindsight-docs/scripts/sync-coding-agents-doc.mjs',
});
