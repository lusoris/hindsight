import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { proxyDataplane, type DataplaneProxyOptions } from "@/lib/dataplane-proxy";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";

const HTTP_OK = 200;
const HTTP_BAD_REQUEST = 400;
const HTTP_NOT_FOUND = 404;
const HTTP_INTERNAL_ERROR = 500;

const URL = "http://localhost/api/test";
const SUFFIX = "/directives/dir-1";

const MISSING = {
  error: "bank_id and directive_id are required",
  errorKey: "api.errors.validation.bankAndDirectiveIdRequired",
};

// localizeApiErrorPayload replaces `error` with the translation of `errorKey`
// whenever the catalogue has one, so the upstream-text cases use a key with no
// translation; otherwise the translated message would hide what the helper sent.
const UNTRANSLATED_FAILURE = {
  error: "Failed to update directive",
  errorKey: "test.directives.update",
};

/** GET options for the directive item, with `overrides` applied. */
function options(overrides: Partial<DataplaneProxyOptions> = {}): DataplaneProxyOptions {
  return {
    required: ["bank-1", "dir-1"],
    missing: MISSING,
    bankId: "bank-1",
    suffix: SUFFIX,
    method: "GET",
    failure: { error: "Failed to get directive", errorKey: "api.errors.directives.fetch" },
    action: "getting directive",
    ...overrides,
  };
}

const PATCH_OPTIONS: Partial<DataplaneProxyOptions> = {
  method: "PATCH",
  failure: UNTRANSLATED_FAILURE,
  action: "updating directive",
  forwardJsonBody: true,
  upstreamTextAsError: true,
};

const DELETE_OPTIONS: Partial<DataplaneProxyOptions> = {
  method: "DELETE",
  failure: { error: "Failed to delete directive", errorKey: "api.errors.directives.delete" },
  action: "deleting directive",
  upstreamTextAsError: true,
  successBody: { success: true },
};

/** A PATCH request carrying `body` as JSON. */
function patchRequest(body: string): Request {
  return new Request(URL, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body,
  });
}

describe("proxyDataplane", () => {
  let fetchSpy: ReturnType<typeof vi.fn<typeof fetch>>;
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;
  const originalFetch = global.fetch;

  beforeEach(() => {
    fetchSpy = vi.fn<typeof fetch>();
    global.fetch = fetchSpy;
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    global.fetch = originalFetch;
    consoleErrorSpy.mockRestore();
  });

  it.each([
    ["empty", ["bank-1", ""]],
    ["undefined", [undefined, "dir-1"]],
  ])("returns 400 with the missing payload when an id is %s", async (_case, required) => {
    const response = await proxyDataplane(new Request(URL), options({ required }));

    expect(response.status).toBe(HTTP_BAD_REQUEST);
    expect(await response.json()).toEqual({ error: MISSING.error });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("GET returns the upstream JSON at 200", async () => {
    const upstream = { id: "dir-1", name: "test directive" };
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(upstream), { status: HTTP_OK }));

    const response = await proxyDataplane(new Request(URL), options());

    expect(response.status).toBe(HTTP_OK);
    expect(await response.json()).toEqual(upstream);
    expect(fetchSpy).toHaveBeenCalledWith(dataplaneBankUrl("bank-1", SUFFIX), {
      method: "GET",
      headers: getDataplaneHeaders(),
    });
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("GET reports the fixed failure at the upstream status and logs the upstream text", async () => {
    fetchSpy.mockResolvedValueOnce(new Response("Not Found", { status: HTTP_NOT_FOUND }));
    // Untranslated key: with a catalogued one, translation would mask upstream text leaking in.
    const failure = { error: "Failed to get directive", errorKey: "test.directives.fetch" };

    const response = await proxyDataplane(new Request(URL), options({ failure }));

    expect(response.status).toBe(HTTP_NOT_FOUND);
    expect(await response.json()).toEqual({ error: failure.error });
    expect(consoleErrorSpy).toHaveBeenCalledWith("API error %s:", "getting directive", "Not Found");
  });

  it("PATCH forwards the JSON body with a JSON Content-Type", async () => {
    const upstream = { id: "dir-1", updated: true };
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(upstream), { status: HTTP_OK }));
    const payload = { title: "Updated Title" };

    const response = await proxyDataplane(
      patchRequest(JSON.stringify(payload)),
      options(PATCH_OPTIONS)
    );

    expect(response.status).toBe(HTTP_OK);
    expect(await response.json()).toEqual(upstream);
    expect(fetchSpy).toHaveBeenCalledWith(dataplaneBankUrl("bank-1", SUFFIX), {
      method: "PATCH",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
  });

  it.each([
    ["the upstream text when there is one", "boom", "boom"],
    ["failure.error when the upstream text is empty", "", UNTRANSLATED_FAILURE.error],
  ])("PATCH non-OK reports %s", async (_case, upstreamText, expected) => {
    fetchSpy.mockResolvedValueOnce(new Response(upstreamText, { status: HTTP_BAD_REQUEST }));

    const response = await proxyDataplane(patchRequest('{"name":"test"}'), options(PATCH_OPTIONS));

    expect(response.status).toBe(HTTP_BAD_REQUEST);
    expect(await response.json()).toEqual({ error: expected });
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "API error %s:",
      "updating directive",
      upstreamText
    );
  });

  it("PATCH with an invalid JSON body returns the failure at 500 without calling upstream", async () => {
    const response = await proxyDataplane(patchRequest("invalid JSON {"), options(PATCH_OPTIONS));

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: UNTRANSLATED_FAILURE.error });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "Error %s:",
      "updating directive",
      expect.any(Error)
    );
  });

  it("DELETE returns successBody at 200 without reading the upstream body", async () => {
    const upstream = new Response(JSON.stringify({ some: "data" }), { status: HTTP_OK });
    const jsonSpy = vi.spyOn(upstream, "json");
    fetchSpy.mockResolvedValueOnce(upstream);

    const response = await proxyDataplane(
      new Request(URL, { method: "DELETE" }),
      options(DELETE_OPTIONS)
    );

    expect(response.status).toBe(HTTP_OK);
    expect(await response.json()).toEqual({ success: true });
    expect(jsonSpy).not.toHaveBeenCalled();
    expect(fetchSpy).toHaveBeenCalledWith(dataplaneBankUrl("bank-1", SUFFIX), {
      method: "DELETE",
      headers: getDataplaneHeaders(),
    });
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("DELETE returns the failure at 500 when fetch rejects", async () => {
    fetchSpy.mockRejectedValueOnce(new Error("Network connection error"));

    const response = await proxyDataplane(
      new Request(URL, { method: "DELETE" }),
      options(DELETE_OPTIONS)
    );

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: "Failed to delete directive" });
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "Error %s:",
      "deleting directive",
      expect.any(Error)
    );
  });
});
