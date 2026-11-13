import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { createKnowledgeBaseNode } from "@/lib/knowledge-base-proxy";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";

const HTTP_OK = 200;
const HTTP_CREATED = 201;
const HTTP_BAD_REQUEST = 400;
const HTTP_CONFLICT = 409;
const HTTP_INTERNAL_ERROR = 500;
const HTTP_BAD_GATEWAY = 502;

const REQUEST_URL = "http://localhost/api/knowledge-base/folders?bank_id=bank-1";

const FAILURE = {
  error: "Failed to create folder",
  errorKey: "api.errors.knowledgeBase.createFolder",
};

// Untranslated key: with a catalogued one, translation would mask failure.error leaking in.
const UNTRANSLATED_FAILURE = {
  error: "Failed to create folder",
  errorKey: "test.knowledgeBase.createFolder",
};

describe("createKnowledgeBaseNode", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    consoleErrorSpy.mockRestore();
  });

  /** Builds the POST the route handler receives; the defaults are a valid create body and URL. */
  function postRequest(
    body: string = JSON.stringify({ name: "x" }),
    url: string = REQUEST_URL
  ): NextRequest {
    return new NextRequest(url, { method: "POST", body });
  }

  /** Makes the next dataplane fetch answer with this body and status, once. */
  function upstreamReturns(body: string, init: ResponseInit): void {
    fetchSpy.mockResolvedValueOnce(new Response(body, init));
  }

  /** Asserts the proxy logged UNTRANSLATED_FAILURE's error text together with the thrown error. */
  function expectLoggedFailure(): void {
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "%s:",
      UNTRANSLATED_FAILURE.error,
      expect.any(Error)
    );
  }

  it("forwards to dataplaneBankUrl with method POST, Content-Type application/json and the same JSON body", async () => {
    const upstream = { id: "f-1", name: "x" };
    upstreamReturns(JSON.stringify(upstream), { status: HTTP_OK });

    const request = postRequest();

    const response = await createKnowledgeBaseNode(request, "folders", FAILURE);

    expect(response.status).toBe(HTTP_OK);
    expect(await response.json()).toEqual(upstream);
    expect(fetchSpy).toHaveBeenCalledWith(dataplaneBankUrl("bank-1", "/knowledge-base/folders"), {
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name: "x" }),
    });
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("percent-encodes a bank id holding path, query, fragment and control characters as one segment", async () => {
    upstreamReturns(JSON.stringify({}), { status: HTTP_OK });
    const hostile = "a/b?c#d\r\n";

    const request = postRequest(
      JSON.stringify({ title: "x" }),
      `http://localhost/api/knowledge-base/pages?bank_id=${encodeURIComponent(hostile)}`
    );

    await createKnowledgeBaseNode(request, "pages", FAILURE);

    const [target] = fetchSpy.mock.calls[0] as [string];
    const url = new URL(target);
    expect(url.pathname).toBe("/v1/default/banks/a%2Fb%3Fc%23d%0D%0A/knowledge-base/pages");
    expect(url.search).toBe("");
    expect(url.hash).toBe("");
  });

  it.each([
    [HTTP_CREATED, { id: "f-1", name: "x" }],
    [HTTP_CONFLICT, { detail: "exists" }],
  ])(
    "upstream %i JSON -> the same status with that JSON, console.error not called",
    async (status, upstream) => {
      upstreamReturns(JSON.stringify(upstream), { status });

      const request = postRequest();

      const response = await createKnowledgeBaseNode(request, "folders", FAILURE);

      expect(response.status).toBe(status);
      expect(await response.json()).toEqual(upstream);
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    }
  );

  it("upstream 502 with a non-JSON body and statusText 'Bad Gateway' -> 502 { detail: 'Bad Gateway' }", async () => {
    upstreamReturns("<html>Bad Gateway</html>", {
      status: HTTP_BAD_GATEWAY,
      statusText: "Bad Gateway",
    });

    const request = postRequest();

    const response = await createKnowledgeBaseNode(request, "folders", FAILURE);

    expect(response.status).toBe(HTTP_BAD_GATEWAY);
    expect(await response.json()).toEqual({ detail: "Bad Gateway" });
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("missing bank_id -> 400, fetch not called", async () => {
    const request = postRequest(undefined, "http://localhost/api/knowledge-base/folders");

    const response = await createKnowledgeBaseNode(request, "folders", FAILURE);

    expect(response.status).toBe(HTTP_BAD_REQUEST);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("fetch rejects -> 500 with failure.error, console.error called with format string", async () => {
    fetchSpy.mockRejectedValueOnce(new Error("Network connection error"));

    const request = postRequest();

    const response = await createKnowledgeBaseNode(request, "folders", UNTRANSLATED_FAILURE);

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: UNTRANSLATED_FAILURE.error });
    expectLoggedFailure();
  });

  it("invalid JSON request body -> 500 with failure.error, fetch not called", async () => {
    const request = postRequest("invalid JSON {");

    const response = await createKnowledgeBaseNode(request, "folders", UNTRANSLATED_FAILURE);

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: UNTRANSLATED_FAILURE.error });
    expect(fetchSpy).not.toHaveBeenCalled();
    expectLoggedFailure();
  });

  it("upstream 200 with a non-JSON body -> 500 with failure.error, console.error called", async () => {
    upstreamReturns("<html>ok</html>", { status: HTTP_OK });

    const response = await createKnowledgeBaseNode(postRequest(), "folders", UNTRANSLATED_FAILURE);

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: UNTRANSLATED_FAILURE.error });
    expectLoggedFailure();
  });
});
