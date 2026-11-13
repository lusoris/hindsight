import { afterEach, beforeEach, vi, type Mock, type MockInstance } from "vitest";

/** The spies a route or proxy suite asserts on: the dataplane `fetch` and `console.error`. */
export type FetchSpies = {
  /** Stands in for global fetch in every test; queue answers with `mockResolvedValueOnce`. */
  fetchSpy: Mock<typeof fetch>;
  /** Receives every console.error call, which prints nothing while a test runs. */
  consoleErrorSpy: Mock<typeof console.error>;
};

/**
 * Registers hooks on the enclosing `describe` that reset both spies, put `fetchSpy` in place of
 * global fetch and route console.error into `consoleErrorSpy` before each test, and put the real
 * fetch and console.error back after it. The returned spies stay the same objects across tests,
 * so a suite can destructure them once.
 */
export function useFetchSpies(): FetchSpies {
  const fetchSpy = vi.fn<typeof fetch>();
  const consoleErrorSpy = vi.fn<typeof console.error>();
  const originalFetch = global.fetch;
  let consoleError: MockInstance<typeof console.error> | undefined;

  beforeEach(() => {
    fetchSpy.mockReset();
    consoleErrorSpy.mockReset();
    global.fetch = fetchSpy;
    consoleError = vi.spyOn(console, "error").mockImplementation(consoleErrorSpy);
  });

  afterEach(() => {
    global.fetch = originalFetch;
    consoleError?.mockRestore();
  });

  return { fetchSpy, consoleErrorSpy };
}
