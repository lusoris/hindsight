import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

type SdkArg = {
  client?: unknown;
  path?: { bank_id: string };
  query?: Record<string, unknown>;
};

const { listDocuments, listEntities, getEntityGraph, listMemories } = vi.hoisted(() => ({
  listDocuments: vi.fn<(arg: SdkArg) => Promise<unknown>>(),
  listEntities: vi.fn<(arg: SdkArg) => Promise<unknown>>(),
  getEntityGraph: vi.fn<(arg: SdkArg) => Promise<unknown>>(),
  listMemories: vi.fn<(bankId: string, options?: Record<string, unknown>) => Promise<unknown>>(),
}));

vi.mock("@/lib/hindsight-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/hindsight-client")>()),
  sdk: { listDocuments, listEntities, getEntityGraph },
  hindsightClient: { listMemories },
}));

vi.mock("@/lib/sdk-response", () => ({
  respondWithSdk: vi.fn(() => new Response(null, { status: 200 })),
}));

import { GET as getDocumentsRoute } from "@/app/api/documents/route";
import { GET as listEntitiesRoute } from "@/app/api/entities/route";
import { GET as getEntityGraphRoute } from "@/app/api/entities/graph/route";
import { GET as listMemoriesRoute } from "@/app/api/list/route";

const BANK_ID = "bank-1";
const NUMERIC_VALUES: Record<string, number> = {
  limit: 7,
  offset: 3,
  min_count: 2,
};

type Handler = (request: NextRequest) => Promise<Response>;

type RouteCase = {
  name: string;
  handler: Handler;
  paramNames: readonly string[];
  reader: () => Record<string, unknown> | undefined;
};

const CASES: RouteCase[] = [
  {
    name: "GET documents",
    handler: getDocumentsRoute,
    paramNames: ["limit", "offset"],
    reader: () => listDocuments.mock.calls[0]?.[0]?.query,
  },
  {
    name: "GET entities",
    handler: listEntitiesRoute,
    paramNames: ["limit", "offset"],
    reader: () => listEntities.mock.calls[0]?.[0]?.query,
  },
  {
    name: "GET entities/graph",
    handler: getEntityGraphRoute,
    paramNames: ["limit", "min_count"],
    reader: () => getEntityGraph.mock.calls[0]?.[0]?.query,
  },
  {
    name: "GET list",
    handler: listMemoriesRoute,
    paramNames: ["limit", "offset"],
    reader: () => listMemories.mock.calls[0]?.[1],
  },
];

/** Builds search params with bank_id and the route's numeric parameters mapped by toValue. */
function buildQuery(
  paramNames: readonly string[],
  toValue: (name: string) => string
): URLSearchParams {
  const searchParams = new URLSearchParams({ bank_id: BANK_ID });
  for (const name of paramNames) {
    searchParams.set(name, toValue(name));
  }
  return searchParams;
}

/** Dispatches a NextRequest with the given search parameters to the route handler. */
function call(handler: Handler, searchParams: URLSearchParams): Promise<Response> {
  const request = new NextRequest(`http://localhost/api/test?${searchParams}`);
  return handler(request);
}

describe("forwarding of migrated numeric query parameters", () => {
  beforeEach(() => {
    listDocuments.mockReset().mockResolvedValue({ data: {}, error: undefined });
    listEntities.mockReset().mockResolvedValue({ data: {}, error: undefined });
    getEntityGraph.mockReset().mockResolvedValue({ data: {}, error: undefined });
    listMemories.mockReset().mockResolvedValue({ items: [] });
  });

  it.each(CASES)(
    "$name forwards distinct numeric query parameters downstream",
    async ({ handler, paramNames, reader }) => {
      const searchParams = buildQuery(paramNames, (name) => String(NUMERIC_VALUES[name]));
      await call(handler, searchParams);
      const forwarded = reader();
      expect(forwarded).toBeDefined();
      for (const name of paramNames) {
        expect(forwarded?.[name]).toBe(NUMERIC_VALUES[name]);
      }
    }
  );

  it.each(CASES)(
    "$name forwards undefined when numeric parameters are non-numeric",
    async ({ handler, paramNames, reader }) => {
      const searchParams = buildQuery(paramNames, () => "abc");
      await call(handler, searchParams);
      const forwarded = reader();
      expect(forwarded).toBeDefined();
      for (const name of paramNames) {
        expect(forwarded?.[name]).toBeUndefined();
      }
    }
  );
});
