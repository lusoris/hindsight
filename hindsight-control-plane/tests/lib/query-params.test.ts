import { describe, expect, it } from "vitest";
import { readOptionalNumber } from "@/lib/query-params";

describe("readOptionalNumber", () => {
  it.each([
    ["absent", ""],
    ["empty", "limit="],
    ["whitespace-only", "limit=%20"],
    ["non-numeric", "limit=abc"],
    ["infinite", "limit=Infinity"],
  ])("returns undefined when the parameter is %s", (_case, query) => {
    expect(readOptionalNumber(new URLSearchParams(query), "limit")).toBeUndefined();
  });

  it("returns the parsed number when the parameter is set", () => {
    expect(readOptionalNumber(new URLSearchParams("limit=0"), "limit")).toBe(0);
    expect(readOptionalNumber(new URLSearchParams("limit=25"), "limit")).toBe(25);
  });
});
