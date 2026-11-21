import { describe, expect, it } from "vitest";
import {
  USER_AGENT,
  dataplaneBankUrl,
  getDataplaneHeaders,
  lowLevelClient,
  sdk,
} from "@/lib/hindsight-client";
import { version } from "../../package.json";
import { useFetchSpies } from "./fetch-spies";

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

describe("lowLevelClient dot-segment barrier", () => {
  const { fetchSpy } = useFetchSpies();

  // Without the interceptor the generated serializer sends `/v1/default/banks/../stats`, which
  // `new Request` resolves to `/v1/default/stats`.
  it.each([".", ".."])("sends nothing for the bank id %j and returns a RangeError", async (bankId) => {
    const result = await sdk.getAgentStats({ client: lowLevelClient, path: { bank_id: bankId } });

    expect(result.error).toBeInstanceOf(RangeError);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("sends an ordinary bank id under its bank path", async () => {
    fetchSpy.mockResolvedValueOnce(new Response("{}", { status: 200 }));

    await sdk.getAgentStats({ client: lowLevelClient, path: { bank_id: "a.b" } });

    expect(fetchSpy).toHaveBeenCalledOnce();
    const sent = fetchSpy.mock.calls[0][0] as Request;
    expect(new URL(sent.url).pathname).toBe("/v1/default/banks/a.b/stats");
  });
});
