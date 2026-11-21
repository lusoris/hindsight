import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { gunzipSync } from "node:zlib";

vi.hoisted(() => {
  process.env.HINDSIGHT_CP_DATAPLANE_API_URL = "http://dataplane";
});

vi.mock("@/lib/hindsight-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/hindsight-client")>()),
  getDataplaneHeaders: () => ({}),
}));

import { GET } from "@/app/api/graph/route";

const graph = { nodes: [{ data: { id: "a" } }], edges: [], table_rows: [], total_units: 1 };

function get(headers: Record<string, string> = {}, signal?: AbortSignal) {
  return GET(
    new NextRequest("http://localhost/api/graph?bank_id=b&type=world&limit=10", {
      headers,
      signal,
    })
  );
}

describe("GET /api/graph", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("gzips the graph when the browser accepts it (#5548)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(graph))));

    const response = await get({ "accept-encoding": "gzip, deflate, br" });

    expect(response.headers.get("content-encoding")).toBe("gzip");
    const body = gunzipSync(Buffer.from(await response.arrayBuffer())).toString();
    expect(JSON.parse(body)).toEqual(graph);
  });

  it("streams it plain to a client that doesn't ask for gzip", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(graph))));

    const response = await get();

    expect(response.headers.get("content-encoding")).toBeNull();
    expect(await response.json()).toEqual(graph);
  });

  it("hands the browser's abort to the dataplane read", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(graph)));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await get({}, controller.signal);
    controller.abort();

    expect(fetchMock.mock.calls[0][0]).toBe("http://dataplane/v1/default/banks/b/graph?type=world&limit=10");
    expect((fetchMock.mock.calls[0][1].signal as AbortSignal).aborted).toBe(true);
  });

  // The route interpolated the raw id before, so `a/../b` reached bank `b`.
  it("keeps a decoded a/../b bank id inside one segment", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(graph)));
    vi.stubGlobal("fetch", fetchMock);

    await GET(new NextRequest("http://localhost/api/graph?bank_id=a%2F..%2Fb"));

    expect(fetchMock.mock.calls[0][0]).toBe("http://dataplane/v1/default/banks/a%2F..%2Fb/graph?");
  });

  it("answers 400 for the bank id .. without calling the dataplane", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(new NextRequest("http://localhost/api/graph?bank_id=.."));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
