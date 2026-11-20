import { describe, expect, it } from "vitest";
import {
  USER_AGENT,
  dataplaneBankUrl,
  getDataplaneHeaders,
  lowLevelClient,
} from "@/lib/hindsight-client";
import { version } from "../../package.json";

describe("dataplane User-Agent", () => {
  it("identifies the control plane and its version", () => {
    expect(USER_AGENT).toBe(`hindsight-control-plane/${version}`);
  });

  it("is sent on direct fetches and by the low-level client", () => {
    expect(getDataplaneHeaders({ "Content-Type": "application/json" })).toMatchObject({
      "User-Agent": USER_AGENT,
      "Content-Type": "application/json",
    });
    const headers = lowLevelClient.getConfig().headers as Headers;
    expect(headers.get("User-Agent")).toBe(USER_AGENT);
  });
});

describe("dataplaneBankUrl", () => {
  it.each([".", ".."])("throws for the dot segment %j instead of leaving the bank path", (bankId) => {
    expect(() => dataplaneBankUrl(bankId, "/stats")).toThrow(RangeError);
  });

  // Only "." and ".." survive encodeURIComponent as dot segments; "%" is encoded to "%25", so the
  // "%2e" spellings a URL parser also reads as dots cannot form.
  it.each(["...", ".a", "a.", "%2e", "%2e%2e", ".%2e", "a/.."])(
    "keeps %j as one bank segment",
    (bankId) => {
      const url = new URL(dataplaneBankUrl(bankId, "/stats"));
      expect(url.pathname).toBe(`/v1/default/banks/${encodeURIComponent(bankId)}/stats`);
    }
  );
});
