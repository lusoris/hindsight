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

  it("forwards to dataplaneBankUrl with method POST, Content-Type application/json and the same JSON body", async () => {
    const upstream = { id: "f-1", name: "x" };
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(upstream), { status: HTTP_OK }));

    const request = new NextRequest(REQUEST_URL, {
      method: "POST",
      body: JSON.stringify({ name: "x" }),
    });

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

  it("upstream 201 JSON -> 201 with that JSON", async () => {
    const upstream = { id: "f-1", name: "x" };
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(upstream), { status: HTTP_CREATED })
    );

    const request = new NextRequest(REQUEST_URL, {
      method: "POST",
      body: JSON.stringify({ name: "x" }),
    });

    const response = await createKnowledgeBaseNode(request, "folders", FAILURE);

    expect(response.status).toBe(HTTP_CREATED);
    expect(await response.json()).toEqual(upstream);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("upstream 409 JSON { detail: 'exists' } -> 409 with that JSON, console.error not called", async () => {
    const upstream = { detail: "exists" };
    fetchSpy.mockResolvedValueOnce(
      new Response(JSON.stringify(upstream), { status: HTTP_CONFLICT })
    );

    const request = new NextRequest(REQUEST_URL, {
      method: "POST",
      body: JSON.stringify({ name: "x" }),
    });

    const response = await createKnowledgeBaseNode(request, "folders", FAILURE);

    expect(response.status).toBe(HTTP_CONFLICT);
    expect(await response.json()).toEqual(upstream);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("upstream 502 with a non-JSON body and statusText 'Bad Gateway' -> 502 { detail: 'Bad Gateway' }", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response("<html>Bad Gateway</html>", {
        status: HTTP_BAD_GATEWAY,
        statusText: "Bad Gateway",
      })
    );

    const request = new NextRequest(REQUEST_URL, {
      method: "POST",
      body: JSON.stringify({ name: "x" }),
    });

    const response = await createKnowledgeBaseNode(request, "folders", FAILURE);

    expect(response.status).toBe(HTTP_BAD_GATEWAY);
    expect(await response.json()).toEqual({ detail: "Bad Gateway" });
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("missing bank_id -> 400, fetch not called", async () => {
    const request = new NextRequest("http://localhost/api/knowledge-base/folders", {
      method: "POST",
      body: JSON.stringify({ name: "x" }),
    });

    const response = await createKnowledgeBaseNode(request, "folders", FAILURE);

    expect(response.status).toBe(HTTP_BAD_REQUEST);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("fetch rejects -> 500 with failure.error, console.error called with format string", async () => {
    fetchSpy.mockRejectedValueOnce(new Error("Network connection error"));

    const request = new NextRequest(REQUEST_URL, {
      method: "POST",
      body: JSON.stringify({ name: "x" }),
    });

    const response = await createKnowledgeBaseNode(request, "folders", UNTRANSLATED_FAILURE);

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: UNTRANSLATED_FAILURE.error });
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "%s:",
      UNTRANSLATED_FAILURE.error,
      expect.any(Error)
    );
  });

  it("invalid JSON request body -> 500 with failure.error, fetch not called", async () => {
    const request = new NextRequest(REQUEST_URL, {
      method: "POST",
      body: "invalid JSON {",
    });

    const response = await createKnowledgeBaseNode(request, "folders", UNTRANSLATED_FAILURE);

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: UNTRANSLATED_FAILURE.error });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "%s:",
      UNTRANSLATED_FAILURE.error,
      expect.any(Error)
    );
  });
});
