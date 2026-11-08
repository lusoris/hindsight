import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const { getEntity, regenerateEntityObservations } = vi.hoisted(() => ({
  getEntity: vi.fn<(arg: { path: { bank_id: string } }) => Promise<unknown>>(),
  regenerateEntityObservations: vi.fn<(arg: { path: { bank_id: string } }) => Promise<unknown>>(),
}));

// Keep the real dataplaneBankUrl/getDataplaneHeaders so the fetch-based routes
// are checked against the URL they really build; only the SDK calls are stubbed.
vi.mock("@/lib/hindsight-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/hindsight-client")>()),
  sdk: { getEntity, regenerateEntityObservations },
}));

vi.mock("@/lib/sdk-response", () => ({
  respondWithSdk: vi.fn(() => new Response(null, { status: 200 })),
}));

import { dataplaneBankUrl } from "@/lib/hindsight-client";
import { GET as getEntityRoute } from "@/app/api/entities/[entityId]/route";
import { POST as regenerateEntityRoute } from "@/app/api/entities/[entityId]/regenerate/route";
import {
  PATCH as patchNodeRoute,
  DELETE as deleteNodeRoute,
} from "@/app/api/knowledge-base/nodes/[nodeId]/route";
import { GET as getPageRoute } from "@/app/api/knowledge-base/pages/[pageId]/route";
import { GET as getMemoryRoute } from "@/app/api/memories/[memoryId]/route";
import { GET as getMemoryHistoryRoute } from "@/app/api/memories/[memoryId]/history/route";

const HTTP_OK = 200;
const HTTP_BAD_REQUEST = 400;
const BANK = "bank-1";

type Handler = (request: NextRequest, context: { params: Promise<never> }) => Promise<Response>;

type RouteCase = {
  name: string;
  handler: Handler;
  method: string;
  params: Record<string, string>;
  /** SDK stub the route calls, or the dataplane path it fetches. */
  downstream: { sdk: typeof getEntity } | { fetchSuffix: string };
};

// Every handler that reads the bank through requireQueryBankId.
const ROUTES: RouteCase[] = [
  {
    name: "GET entities/[entityId]",
    handler: getEntityRoute as Handler,
    method: "GET",
    params: { entityId: "e-1" },
    downstream: { sdk: getEntity },
  },
  {
    name: "POST entities/[entityId]/regenerate",
    handler: regenerateEntityRoute as Handler,
    method: "POST",
    params: { entityId: "e-1" },
    downstream: { sdk: regenerateEntityObservations },
  },
  {
    name: "PATCH knowledge-base/nodes/[nodeId]",
    handler: patchNodeRoute as Handler,
    method: "PATCH",
    params: { nodeId: "n-1" },
    downstream: { fetchSuffix: "/knowledge-base/nodes/n-1" },
  },
  {
    name: "DELETE knowledge-base/nodes/[nodeId]",
    handler: deleteNodeRoute as Handler,
    method: "DELETE",
    params: { nodeId: "n-1" },
    downstream: { fetchSuffix: "/knowledge-base/nodes/n-1" },
  },
  {
    name: "GET knowledge-base/pages/[pageId]",
    handler: getPageRoute as Handler,
    method: "GET",
    params: { pageId: "p-1" },
    downstream: { fetchSuffix: "/knowledge-base/pages/p-1" },
  },
  {
    name: "GET memories/[memoryId]",
    handler: getMemoryRoute as Handler,
    method: "GET",
    params: { memoryId: "m-1" },
    downstream: { fetchSuffix: "/memories/m-1" },
  },
  {
    name: "GET memories/[memoryId]/history",
    handler: getMemoryHistoryRoute as Handler,
    method: "GET",
    params: { memoryId: "m-1" },
    downstream: { fetchSuffix: "/memories/m-1/history" },
  },
];

function call(route: RouteCase, query: string): Promise<Response> {
  const request = new NextRequest(`http://localhost/api/test${query}`, {
    method: route.method,
    // The PATCH route parses a JSON body before it forwards.
    ...(route.method === "PATCH" ? { body: "{}" } : {}),
  });
  return route.handler(request, { params: Promise.resolve(route.params) as Promise<never> });
}

describe("routes reading the bank from ?bank_id=", () => {
  const originalFetch = global.fetch;
  let fetchSpy: ReturnType<typeof vi.fn<typeof fetch>>;

  beforeEach(() => {
    getEntity.mockReset().mockResolvedValue({ data: {}, error: undefined });
    regenerateEntityObservations.mockReset().mockResolvedValue({ data: {}, error: undefined });
    fetchSpy = vi.fn<typeof fetch>(async () => new Response("{}", { status: HTTP_OK }));
    global.fetch = fetchSpy;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it.each(ROUTES)("$name returns the shared 400 without calling downstream", async (route) => {
    const response = await call(route, "");

    expect(response.status).toBe(HTTP_BAD_REQUEST);
    expect(await response.json()).toEqual({ error: "bank_id is required" });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(getEntity).not.toHaveBeenCalled();
    expect(regenerateEntityObservations).not.toHaveBeenCalled();
  });

  it.each(ROUTES)("$name passes the query bank_id downstream", async (route) => {
    const response = await call(route, `?bank_id=${BANK}`);

    expect(response.status).toBe(HTTP_OK);
    if ("sdk" in route.downstream) {
      expect(route.downstream.sdk).toHaveBeenCalledTimes(1);
      expect(route.downstream.sdk.mock.calls[0][0].path.bank_id).toBe(BANK);
    } else {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(fetchSpy.mock.calls[0][0]).toBe(dataplaneBankUrl(BANK, route.downstream.fetchSuffix));
    }
  });
});
