import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

type SdkStub = ReturnType<typeof vi.fn<(arg: { path: { bank_id: string } }) => Promise<unknown>>>;

const { getEntity, regenerateEntityObservations, listEntities, getEntityGraph, listMemories } =
  vi.hoisted(() => ({
    getEntity: vi.fn<(arg: { path: { bank_id: string } }) => Promise<unknown>>(),
    regenerateEntityObservations: vi.fn<(arg: { path: { bank_id: string } }) => Promise<unknown>>(),
    listEntities: vi.fn<(arg: { path: { bank_id: string } }) => Promise<unknown>>(),
    getEntityGraph: vi.fn<(arg: { path: { bank_id: string } }) => Promise<unknown>>(),
    listMemories: vi.fn<(bankId: string, options: unknown) => Promise<unknown>>(),
  }));

// Keep the real dataplaneBankUrl/getDataplaneHeaders so the fetch-based routes
// are checked against the URL they really build; only the SDK calls are stubbed.
vi.mock("@/lib/hindsight-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/hindsight-client")>()),
  sdk: { getEntity, regenerateEntityObservations, listEntities, getEntityGraph },
  hindsightClient: { listMemories },
}));

vi.mock("@/lib/sdk-response", () => ({
  respondWithSdk: vi.fn(() => new Response(null, { status: 200 })),
}));

import { dataplaneBankUrl } from "@/lib/hindsight-client";
import { GET as getEntityRoute } from "@/app/api/entities/[entityId]/route";
import { POST as regenerateEntityRoute } from "@/app/api/entities/[entityId]/regenerate/route";
import { GET as listEntitiesRoute } from "@/app/api/entities/route";
import { GET as getEntityGraphRoute } from "@/app/api/entities/graph/route";
import { GET as getGraphRoute } from "@/app/api/graph/route";
import { GET as listMemoriesRoute } from "@/app/api/list/route";
import {
  GET as exportDocumentsRoute,
  POST as importDocumentsRoute,
} from "@/app/api/documents/transfer/route";
import { GET as exportKnowledgeBaseRoute } from "@/app/api/knowledge-base/export/route";
import { POST as createFolderRoute } from "@/app/api/knowledge-base/folders/route";
import { POST as createPageRoute } from "@/app/api/knowledge-base/pages/route";
import { GET as searchKnowledgeBaseRoute } from "@/app/api/knowledge-base/search/route";
import { GET as getTreeRoute } from "@/app/api/knowledge-base/tree/route";
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

type GuardCase = {
  name: string;
  handler: Handler;
  method: string;
  params?: Record<string, string>;
  /** Query parameters the route needs besides the bank, e.g. a search's `q`. */
  query?: Record<string, string>;
  /** JSON body for the routes that parse one before they forward. */
  body?: string;
};

type RouteCase = GuardCase & {
  /** SDK stub the route calls, the hindsightClient call, or the dataplane path it fetches. */
  downstream: { sdk: SdkStub } | { client: typeof listMemories } | { fetchSuffix: string };
};

// Every handler that reads the bank through requireQueryBankId and makes one
// downstream call for it.
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
    name: "GET entities",
    handler: listEntitiesRoute as Handler,
    method: "GET",
    downstream: { sdk: listEntities },
  },
  {
    name: "GET entities/graph",
    handler: getEntityGraphRoute as Handler,
    method: "GET",
    downstream: { sdk: getEntityGraph },
  },
  {
    name: "GET graph",
    handler: getGraphRoute as Handler,
    method: "GET",
    downstream: { fetchSuffix: "/graph?" },
  },
  {
    name: "GET list",
    handler: listMemoriesRoute as Handler,
    method: "GET",
    downstream: { client: listMemories },
  },
  {
    name: "GET knowledge-base/export",
    handler: exportKnowledgeBaseRoute as Handler,
    method: "GET",
    downstream: { fetchSuffix: "/knowledge-base/export" },
  },
  {
    name: "POST knowledge-base/folders",
    handler: createFolderRoute as Handler,
    method: "POST",
    body: "{}",
    downstream: { fetchSuffix: "/knowledge-base/folders" },
  },
  {
    name: "POST knowledge-base/pages",
    handler: createPageRoute as Handler,
    method: "POST",
    body: "{}",
    downstream: { fetchSuffix: "/knowledge-base/pages" },
  },
  {
    name: "GET knowledge-base/search",
    handler: searchKnowledgeBaseRoute as Handler,
    method: "GET",
    // A blank `q` short-circuits to an empty result before any upstream call.
    query: { q: "x" },
    downstream: { fetchSuffix: "/knowledge-base/search?q=x&limit=10" },
  },
  {
    name: "GET knowledge-base/tree",
    handler: getTreeRoute as Handler,
    method: "GET",
    downstream: { fetchSuffix: "/knowledge-base/tree?" },
  },
  {
    name: "PATCH knowledge-base/nodes/[nodeId]",
    handler: patchNodeRoute as Handler,
    method: "PATCH",
    params: { nodeId: "n-1" },
    body: "{}",
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

// The transfer routes also read the bank through requireQueryBankId, but past
// the guard they run a multi-request export poll or a multipart import, so only
// their missing-bank answer is checked here.
const GUARD_ONLY_ROUTES: GuardCase[] = [
  { name: "GET documents/transfer", handler: exportDocumentsRoute as Handler, method: "GET" },
  { name: "POST documents/transfer", handler: importDocumentsRoute as Handler, method: "POST" },
];

// Routes that still accept the legacy `?agent_id=` in place of `?bank_id=`.
const AGENT_ID_ROUTES = ROUTES.filter((route) => ["GET graph", "GET list"].includes(route.name));

function call(route: GuardCase, bank: Record<string, string>): Promise<Response> {
  const query = new URLSearchParams({ ...route.query, ...bank });
  const request = new NextRequest(`http://localhost/api/test?${query}`, {
    method: route.method,
    ...(route.body === undefined ? {} : { body: route.body }),
  });
  return route.handler(request, { params: Promise.resolve(route.params ?? {}) as Promise<never> });
}

/** Asserts that the route's one downstream call went to bank `BANK`. */
function expectDownstreamBank(route: RouteCase, fetchSpy: ReturnType<typeof vi.fn<typeof fetch>>) {
  if ("sdk" in route.downstream) {
    expect(route.downstream.sdk).toHaveBeenCalledTimes(1);
    expect(route.downstream.sdk.mock.calls[0][0].path.bank_id).toBe(BANK);
  } else if ("client" in route.downstream) {
    expect(route.downstream.client).toHaveBeenCalledTimes(1);
    expect(route.downstream.client.mock.calls[0][0]).toBe(BANK);
  } else {
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe(dataplaneBankUrl(BANK, route.downstream.fetchSuffix));
  }
}

describe("routes reading the bank from ?bank_id=", () => {
  const originalFetch = global.fetch;
  let fetchSpy: ReturnType<typeof vi.fn<typeof fetch>>;
  const sdkStubs = [getEntity, regenerateEntityObservations, listEntities, getEntityGraph];

  beforeEach(() => {
    for (const stub of sdkStubs) stub.mockReset().mockResolvedValue({ data: {}, error: undefined });
    listMemories.mockReset().mockResolvedValue({ items: [] });
    fetchSpy = vi.fn<typeof fetch>(async () => new Response("{}", { status: HTTP_OK }));
    global.fetch = fetchSpy;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it.each([...ROUTES, ...GUARD_ONLY_ROUTES])(
    "$name returns the shared 400 without calling downstream",
    async (route) => {
      const response = await call(route, {});

      expect(response.status).toBe(HTTP_BAD_REQUEST);
      expect(await response.json()).toEqual({ error: "bank_id is required" });
      expect(fetchSpy).not.toHaveBeenCalled();
      for (const stub of sdkStubs) expect(stub).not.toHaveBeenCalled();
      expect(listMemories).not.toHaveBeenCalled();
    }
  );

  it.each(ROUTES)("$name passes the query bank_id downstream", async (route) => {
    const response = await call(route, { bank_id: BANK });

    expect(response.status).toBe(HTTP_OK);
    expectDownstreamBank(route, fetchSpy);
  });

  it.each(AGENT_ID_ROUTES)("$name accepts the legacy ?agent_id=", async (route) => {
    const response = await call(route, { agent_id: BANK });

    expect(response.status).toBe(HTTP_OK);
    expectDownstreamBank(route, fetchSpy);
  });
});
