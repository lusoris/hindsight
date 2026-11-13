import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import postcss, { type Root } from "postcss";
import tailwind from "@tailwindcss/postcss";

/**
 * Compile src/app/globals.css through Tailwind v4 and return the resulting AST.
 * The style tests read the compiled output rather than the source: Tailwind v4
 * only applies what the CSS entry loads, so the source alone cannot show what
 * actually ships.
 */
export async function compileGlobals(): Promise<Root> {
  const stylesheet = fileURLToPath(new URL("../../src/app/globals.css", import.meta.url));
  const base = fileURLToPath(new URL("../../", import.meta.url));
  const result = await postcss([tailwind({ base })]).process(await readFile(stylesheet, "utf8"), {
    from: stylesheet,
  });
  return result.root;
}
