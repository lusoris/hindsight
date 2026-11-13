import { describe, expect, it } from "vitest";
import { useFetchSpies } from "./fetch-spies";

const realFetch = global.fetch;
const realConsoleError = console.error;

// Vitest runs a file's tests in the order they are declared, so the last test sees the globals
// after the inner suite's hooks have run.
describe("useFetchSpies", () => {
  describe("inside a suite that calls it", () => {
    const { fetchSpy, consoleErrorSpy } = useFetchSpies();

    it("puts fetchSpy in place of fetch and routes console.error into consoleErrorSpy", async () => {
      fetchSpy.mockResolvedValueOnce(new Response("{}"));

      await fetch("http://localhost/x");
      console.error("logged %s", "here");

      expect(global.fetch).toBe(fetchSpy);
      expect(fetchSpy).toHaveBeenCalledWith("http://localhost/x");
      expect(consoleErrorSpy).toHaveBeenCalledWith("logged %s", "here");
    });

    it("starts each test with both spies reset", () => {
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    });
  });

  it("puts the real fetch and console.error back after the suite's tests", () => {
    expect(global.fetch).toBe(realFetch);
    expect(console.error).toBe(realConsoleError);
  });
});
