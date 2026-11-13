import { describe, expect, it, vi } from "vitest";
import { NextResponse } from "next/server";
import {
  proxyDataplane,
  relayBankPost,
  type BankPostCall,
  type DataplaneProxyOptions,
} from "@/lib/dataplane-proxy";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { useFetchSpies } from "./fetch-spies";

const HTTP_OK = 200;
const HTTP_ACCEPTED = 202;
const HTTP_BAD_REQUEST = 400;
const HTTP_NOT_FOUND = 404;
const HTTP_CONFLICT = 409;
const HTTP_INTERNAL_ERROR = 500;
const HTTP_BAD_GATEWAY = 502;
const HTTP_SERVICE_UNAVAILABLE = 503;

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

const NOT_FOUND_FAILURE = {
  error: "Directive not found",
  errorKey: "test.directives.notFound",
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
  const { fetchSpy, consoleErrorSpy } = useFetchSpies();

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

  it.each([
    { label: "alone", extra: {} },
    { label: "with nonOkAsServerError", extra: { nonOkAsServerError: true } },
  ])(
    "notFound ($label) returns the notFound payload at 404 without reading upstream text or logging",
    async ({ extra }) => {
      const upstream = new Response("upstream 404 body", { status: HTTP_NOT_FOUND });
      const textSpy = vi.spyOn(upstream, "text");
      fetchSpy.mockResolvedValueOnce(upstream);

      const response = await proxyDataplane(
        new Request(URL),
        options({ notFound: NOT_FOUND_FAILURE, ...extra })
      );

      expect(response.status).toBe(HTTP_NOT_FOUND);
      expect(await response.json()).toEqual({ error: NOT_FOUND_FAILURE.error });
      expect(textSpy).not.toHaveBeenCalled();
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    }
  );

  it("notFound on upstream 500 without nonOkAsServerError retains non-OK behavior", async () => {
    fetchSpy.mockResolvedValueOnce(new Response("Upstream Error", { status: HTTP_INTERNAL_ERROR }));
    const failure = { error: "Failed to get directive", errorKey: "test.directives.fetch" };

    const response = await proxyDataplane(
      new Request(URL),
      options({ failure, notFound: NOT_FOUND_FAILURE })
    );

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: failure.error });
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "API error %s:",
      "getting directive",
      "Upstream Error"
    );
  });

  it("nonOkAsServerError answers failure at 500 and logs Error for upstream 503", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response("Service Unavailable", { status: HTTP_SERVICE_UNAVAILABLE })
    );
    const failure = { error: "Failed to get directive", errorKey: "test.directives.fetch" };

    const response = await proxyDataplane(
      new Request(URL),
      options({ failure, nonOkAsServerError: true })
    );

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: failure.error });
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "Error %s:",
      "getting directive",
      expect.any(Error)
    );
  });

  it.each([
    {
      label: "sends no body or Content-Type when jsonBody is omitted",
      extra: {},
      expectedInit: { method: "POST", headers: getDataplaneHeaders() },
    },
    {
      label: "sends no body or Content-Type when jsonBody is undefined",
      extra: { jsonBody: undefined },
      expectedInit: { method: "POST", headers: getDataplaneHeaders() },
    },
    {
      label: "sends JSON.stringify(jsonBody) with Content-Type application/json",
      extra: { jsonBody: { mode: "full" } },
      expectedInit: {
        method: "POST",
        headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ mode: "full" }),
      },
    },
    {
      label: "sends JSON.stringify(jsonBody) when jsonBody is falsy but defined (null)",
      extra: { jsonBody: null },
      expectedInit: {
        method: "POST",
        headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
        body: "null",
      },
    },
  ])("POST $label", async ({ extra, expectedInit }) => {
    const result = { status: "queued" };
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(result), { status: HTTP_OK }));

    const response = await proxyDataplane(
      new Request(URL, { method: "POST" }),
      options({ method: "POST", action: "refreshing mental model", ...extra })
    );

    expect(response.status).toBe(HTTP_OK);
    expect(await response.json()).toEqual(result);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [calledUrl, calledInit] = fetchSpy.mock.calls[0];
    expect(calledUrl).toBe(dataplaneBankUrl("bank-1", SUFFIX));
    expect(calledInit).toStrictEqual(expectedInit);
  });
});

describe("relayBankPost", () => {
  const { fetchSpy, consoleErrorSpy } = useFetchSpies();

  // Bank id with a path separator and a space: the relay must hand it to dataplaneBankUrl as is.
  const BANK_ID = "team/bank 1";
  const RELAY_SUFFIX = "/clone?target_bank_id=copy";
  const RELAY_OPTIONS = {
    successStatus: HTTP_ACCEPTED,
    failure: UNTRANSLATED_FAILURE,
    action: "cloning bank",
  };

  /** Runs the relay for BANK_ID with a `prepare` that answers `call` (or throws it when an Error). */
  function relay(call: BankPostCall | NextResponse | Error): Promise<NextResponse> {
    return relayBankPost(
      new Request(URL, { method: "POST" }),
      { params: Promise.resolve({ bankId: BANK_ID }) },
      RELAY_OPTIONS,
      async (bankId) => {
        expect(bankId).toBe(BANK_ID);
        if (call instanceof Error) throw call;
        return call;
      }
    );
  }

  it.each([
    {
      label: "without a body sends POST with the plain dataplane headers",
      call: { suffix: RELAY_SUFFIX },
      expectedInit: { method: "POST", headers: getDataplaneHeaders() },
    },
    {
      label: "with a JSON body sends it with a JSON Content-Type",
      call: { suffix: RELAY_SUFFIX, jsonBody: { manifest: 1 } },
      expectedInit: {
        method: "POST",
        headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ manifest: 1 }),
      },
    },
  ])("$label and returns the upstream JSON at successStatus", async ({ call, expectedInit }) => {
    const upstream = { operation_id: "op-1", status: "pending" };
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(upstream), { status: HTTP_OK }));

    const response = await relay(call);

    expect(response.status).toBe(HTTP_ACCEPTED);
    expect(await response.json()).toEqual(upstream);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [calledUrl, calledInit] = fetchSpy.mock.calls[0];
    expect(calledUrl).toBe(dataplaneBankUrl(BANK_ID, RELAY_SUFFIX));
    expect(calledInit).toStrictEqual(expectedInit);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("returns an early response from prepare unchanged without calling the dataplane", async () => {
    const early = NextResponse.json({ error: "target_bank_id is required" }, { status: 400 });

    const response = await relay(early);

    expect(response).toBe(early);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([
    {
      label: "the upstream error JSON",
      upstream: new Response(JSON.stringify({ detail: "Bank exists" }), { status: HTTP_CONFLICT }),
      status: HTTP_CONFLICT,
      body: { detail: "Bank exists" },
    },
    {
      label: "the status text when the upstream error is not JSON",
      upstream: new Response("<html>bad gateway</html>", {
        status: HTTP_BAD_GATEWAY,
        statusText: "Bad Gateway",
      }),
      status: HTTP_BAD_GATEWAY,
      body: { detail: "Bad Gateway" },
    },
  ])("relays $label at the upstream status", async ({ upstream, status, body }) => {
    fetchSpy.mockResolvedValueOnce(upstream);

    const response = await relay({ suffix: RELAY_SUFFIX });

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it.each([
    {
      label: "prepare throws",
      call: new Error("bad request body"),
      arrange: () => {},
      fetched: false,
    },
    {
      label: "the dataplane call rejects",
      call: { suffix: RELAY_SUFFIX },
      arrange: () => fetchSpy.mockRejectedValueOnce(new Error("connection refused")),
      fetched: true,
    },
    {
      label: "an upstream success is not JSON",
      call: { suffix: RELAY_SUFFIX },
      arrange: () => fetchSpy.mockResolvedValueOnce(new Response("ok", { status: HTTP_OK })),
      fetched: true,
    },
  ])("answers the failure at 500 and logs the action when $label", async (testCase) => {
    testCase.arrange();

    const response = await relay(testCase.call);

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: UNTRANSLATED_FAILURE.error });
    expect(fetchSpy).toHaveBeenCalledTimes(testCase.fetched ? 1 : 0);
    expect(consoleErrorSpy).toHaveBeenCalledWith("Error %s:", "cloning bank", expect.any(Error));
  });
});
