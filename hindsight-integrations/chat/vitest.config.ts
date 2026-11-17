import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "node",
    include: ["src/**/*.test.ts"],
    // Type-level tests (*.test-d.ts) only fail when tsc checks them: plain `vitest run`
    // treats expectTypeOf and @ts-expect-error as no-ops, and tsconfig.json excludes *.test.ts.
    typecheck: { enabled: true },
  },
});
