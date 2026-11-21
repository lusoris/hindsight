import { describe, expect, it } from "vitest";
import { forwardParams, readOptionalNumber } from "@/lib/query-params";

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

describe("forwardParams", () => {
  it("appends repeated keys first, then first single values in the declared order", () => {
    const input = new URLSearchParams(
      "offset=0&limit=5&tags=b&tags=&tags=a&limit=9&tags_match=bogus&other=x"
    );
    const forwarded = forwardParams(input, {
      repeated: ["tags"],
      single: ["tags_match", "limit", "offset"],
    });
    expect(forwarded.toString()).toBe("tags=b&tags=&tags=a&tags_match=bogus&limit=5&offset=0");
    expect(input.toString()).toBe(
      "offset=0&limit=5&tags=b&tags=&tags=a&limit=9&tags_match=bogus&other=x"
    );
  });

  it("skips absent and empty single values without falling through to later values", () => {
    expect(
      forwardParams(new URLSearchParams("limit=&limit=9&offset="), {
        single: ["limit", "offset", "q"],
      }).toString()
    ).toBe("");
  });

  it("keeps unknown tags_match without tags and does not trim or coerce values", () => {
    expect(
      forwardParams(new URLSearchParams("tags_match=bogus&q=+&limit=00&source=a%2Fb"), {
        single: ["tags_match", "q", "limit", "source"],
      }).toString()
    ).toBe("tags_match=bogus&q=+&limit=00&source=a%2Fb");
  });

  it("supports multiple repeated keys, preserving each key's value order", () => {
    expect(
      forwardParams(new URLSearchParams("b=2&a=1&b=3&a=4"), {
        repeated: ["a", "b"],
      }).toString()
    ).toBe("a=1&a=4&b=2&b=3");
  });

  it("forwards nothing when no keys are selected", () => {
    expect(forwardParams(new URLSearchParams("tags=a&q=x"), {}).toString()).toBe("");
  });
});
