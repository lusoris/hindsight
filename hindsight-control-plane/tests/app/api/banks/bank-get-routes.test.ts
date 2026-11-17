import { beforeEach, describe, expect, it, vi } from "vitest";
import { format } from "node:util";

// Missing translations must expose upstream detail instead of masking it with a catalogue message.
// Other locales remain real so the localization contract is exercised below too.
vi.mock("@/messages/en.json", () => ({ default: {} }));

import { GET as auditLogs } from "@/app/api/banks/[bankId]/audit-logs/route";
import { GET as auditStats } from "@/app/api/banks/[bankId]/audit-logs/stats/route";
import { GET as llmRequests } from "@/app/api/banks/[bankId]/llm-requests/route";
import { GET as llmStats } from "@/app/api/banks/[bankId]/llm-requests/stats/route";
import { GET as directives } from "@/app/api/banks/[bankId]/directives/route";
import { GET as mentalModels } from "@/app/api/banks/[bankId]/mental-models/route";
import { GET as scopes } from "@/app/api/banks/[bankId]/observations/scopes/route";
import { GET as tags } from "@/app/api/banks/[bankId]/tags/route";
import { DATAPLANE_URL, getDataplaneHeaders } from "@/lib/hindsight-client";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { useFetchSpies } from "../../../lib/fetch-spies";

const WHOLE_QUERY = "tags=a&empty=&tags=b&tags_match=bogus&limit=0&unknown=x+y";
const FILTER_QUERY =
  "offset=4&limit=0&tags=b&tags=&tags=a&tags_match=bogus&detail=full&exclude_knowledge_pages=false&unknown=x";
const ROUTES = [
  {
    name: "audit-logs",
    handler: auditLogs,
    mode: "detail",
    action: "listing audit logs",
    error: "Failed to list audit logs",
    errorKey: "api.errors.auditLogs.list",
    query: WHOLE_QUERY,
    forwarded: WHOLE_QUERY,
  },
  {
    name: "audit-logs/stats",
    handler: auditStats,
    mode: "detail",
    action: "getting audit log stats",
    error: "Failed to get audit log stats",
    errorKey: "api.errors.auditLogs.stats",
    query: WHOLE_QUERY,
    forwarded: WHOLE_QUERY,
  },
  {
    name: "llm-requests",
    handler: llmRequests,
    mode: "detail",
    action: "listing LLM requests",
    error: "Failed to list LLM requests",
    errorKey: "api.errors.llmRequests.list",
    query: WHOLE_QUERY,
    forwarded: WHOLE_QUERY,
  },
  {
    name: "llm-requests/stats",
    handler: llmStats,
    mode: "detail",
    action: "getting LLM request stats",
    error: "Failed to get LLM request stats",
    errorKey: "api.errors.llmRequests.stats",
    query: WHOLE_QUERY,
    forwarded: WHOLE_QUERY,
  },
  {
    name: "directives",
    handler: directives,
    mode: "log",
    action: "listing directives",
    error: "Failed to list directives",
    errorKey: "api.errors.directives.list",
    query: FILTER_QUERY,
    forwarded: "tags=b&tags=&tags=a&tags_match=bogus&limit=0&offset=4",
  },
  {
    name: "mental-models",
    handler: mentalModels,
    mode: "log",
    action: "listing mental models",
    error: "Failed to list mental models",
    errorKey: "api.errors.mentalModels.list",
    query: FILTER_QUERY,
    forwarded:
      "tags=b&tags=&tags=a&tags_match=bogus&detail=full&limit=0&offset=4&exclude_knowledge_pages=false",
  },
  {
    name: "tags",
    handler: tags,
    mode: "log",
    action: "listing tags",
    error: "Failed to list tags",
    errorKey: "api.errors.tags.list",
    query: "offset=4&source=observation&q=a%2Fb+z&limit=0&q=ignored&unknown=x",
    forwarded: "q=a%2Fb+z&source=observation&limit=0&offset=4",
  },
  {
    name: "observations/scopes",
    handler: scopes,
    mode: "relay",
    action: "listing observation scopes",
    error: "Failed to list observation scopes",
    errorKey: "api.errors.observations.list",
    query: "offset=4&limit=0&limit=ignored&tags=x",
    forwarded: "limit=0&offset=4",
  },
];
const context = (bankId = "bank-1") => ({ params: Promise.resolve({ bankId }) });
const request = (query = "") => new Request(`http://localhost/api/test${query ? `?${query}` : ""}`);

async function expectCaughtFailure(
  response: Response,
  route: (typeof ROUTES)[number],
  lines: string[]
) {
  expect(response.status).toBe(500);
  expect(await response.json()).toEqual({ error: route.error });
  expect(lines[0]).toContain(`Error ${route.action}:`);
}

for (const route of ROUTES) {
  describe(`GET ${route.name}`, () => {
    const { fetchSpy, consoleErrorSpy } = useFetchSpies();
    let lines: string[];
    beforeEach(() => {
      lines = [];
      consoleErrorSpy.mockImplementation((...args) => {
        lines.push(format(...args));
      });
    });

    it("forwards the exact query and returns OK JSON at 200", async () => {
      fetchSpy.mockResolvedValueOnce(Response.json({ items: [1] }, { status: 201 }));
      const response = await route.handler(request(route.query), context());
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({ items: [1] });
      expect(fetchSpy).toHaveBeenCalledOnce();
      const [url, init] = fetchSpy.mock.calls[0];
      expect(url).toBe(`${DATAPLANE_URL}/v1/default/banks/bank-1/${route.name}?${route.forwarded}`);
      // Scopes historically omitted method; explicit GET is equivalent on the wire.
      expect(init?.method ?? "GET").toBe("GET");
      expect(init?.headers).toEqual(getDataplaneHeaders());
      expect(lines).toEqual([]);
    });

    it("adds no question mark for an absent query", async () => {
      fetchSpy.mockResolvedValueOnce(Response.json({}));
      await route.handler(request(), context());
      expect(fetchSpy.mock.calls[0][0]).toBe(
        `${DATAPLANE_URL}/v1/default/banks/bank-1/${route.name}`
      );
    });

    // Before relayBankGet, observations/scopes interpolated the raw bank id into the URL. Every
    // route now builds it with dataplaneBankUrl, so a reserved character cannot change the path.
    it("URL-encodes the bank id in the upstream path", async () => {
      fetchSpy.mockResolvedValueOnce(Response.json({}));
      await route.handler(request(), context("team a/b?x"));
      expect(fetchSpy.mock.calls[0][0]).toBe(
        `${DATAPLANE_URL}/v1/default/banks/team%20a%2Fb%3Fx/${route.name}`
      );
    });

    it("answers a thrown fetch with its failure at 500 and the original log label", async () => {
      fetchSpy.mockRejectedValueOnce(new Error("connection refused"));
      const response = await route.handler(request(), context());
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual({ error: route.error });
      expect(lines).toHaveLength(1);
      expect(lines[0]).toContain(`Error ${route.action}: Error: connection refused`);
    });

    it("validates an empty bank id before fetching", async () => {
      const response = await route.handler(request(), context(""));
      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ error: "bank_id is required" });
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(lines).toEqual([]);
    });

    it("localizes its failure for a supported language", async () => {
      const req = new Request("http://localhost/api/test", {
        headers: { "accept-language": "de" },
      });
      fetchSpy.mockRejectedValueOnce(new Error("offline"));
      const response = await route.handler(req, context());
      expect(await response.json()).toEqual(
        localizeApiErrorPayload(req, { error: route.error, errorKey: route.errorKey })
      );
    });

    it("catches non-JSON successes at 500", async () => {
      fetchSpy.mockResolvedValueOnce(new Response("not JSON"));
      const response = await route.handler(request(), context());
      await expectCaughtFailure(response, route, lines);
    });

    if (route.mode === "detail") {
      it.each(["upstream detail", "", undefined])(
        "uses detail %s or its fallback at the upstream status",
        async (detail) => {
          fetchSpy.mockResolvedValueOnce(Response.json({ detail }, { status: 422 }));
          const response = await route.handler(request(), context());
          expect(response.status).toBe(422);
          expect(await response.json()).toEqual({ error: detail || route.error });
          expect(lines).toEqual([]);
        }
      );
      it("parses JSON before checking non-OK and catches malformed JSON at 500", async () => {
        fetchSpy.mockResolvedValueOnce(new Response("broken", { status: 502 }));
        const response = await route.handler(request(), context());
        await expectCaughtFailure(response, route, lines);
      });
    } else if (route.mode === "log") {
      it("logs non-OK text and answers its failure at the upstream status", async () => {
        fetchSpy.mockResolvedValueOnce(new Response("upstream failure", { status: 503 }));
        const response = await route.handler(request(), context());
        expect(response.status).toBe(503);
        expect(await response.json()).toEqual({ error: route.error });
        expect(lines).toEqual([`API error ${route.action}: upstream failure`]);
      });
    } else {
      it.each([
        {
          body: JSON.stringify({ detail: "missing", extra: 1 }),
          expected: { detail: "missing", extra: 1 },
        },
        { body: "not JSON", expected: { detail: "Bad Gateway" } },
      ])("relays non-OK JSON or status text: $body", async ({ body, expected }) => {
        fetchSpy.mockResolvedValueOnce(
          new Response(body, { status: 502, statusText: "Bad Gateway" })
        );
        const response = await route.handler(request(), context());
        expect(response.status).toBe(502);
        expect(await response.json()).toEqual(expected);
        expect(lines).toEqual([]);
      });
    }

    if (route.mode !== "detail") {
      it("skips empty single values, keeps the first single value, and preserves whitespace", async () => {
        const query =
          "tags_match=bogus&limit=&limit=9&offset=+&q=&source=&detail=&exclude_knowledge_pages=";
        const expected =
          route.name === "directives" || route.name === "mental-models"
            ? "tags_match=bogus&offset=+"
            : "offset=+";
        fetchSpy.mockResolvedValueOnce(Response.json({}));
        await route.handler(request(query), context());
        expect(fetchSpy.mock.calls[0][0]).toBe(
          `${DATAPLANE_URL}/v1/default/banks/bank-1/${route.name}?${expected}`
        );
      });
      it("skips all empty single values without adding a question mark", async () => {
        fetchSpy.mockResolvedValueOnce(Response.json({}));
        await route.handler(
          request("tags_match=&limit=&offset=&q=&source=&detail=&exclude_knowledge_pages="),
          context()
        );
        expect(fetchSpy.mock.calls[0][0]).toBe(
          `${DATAPLANE_URL}/v1/default/banks/bank-1/${route.name}`
        );
      });
    }
  });
}
