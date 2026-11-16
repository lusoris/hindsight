import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { POST as previewStrategies } from "@/app/api/banks/[bankId]/consolidation-strategies/preview/route";
import { GET as getGraph } from "@/app/api/graph/route";
import { GET as getAttachment } from "@/app/api/banks/[bankId]/attachments/[attachmentId]/route";
import { GET as downloadFile } from "@/app/api/files/download/route";
import { useFetchSpies } from "../../lib/fetch-spies";

const HTTP_OK = 200;
const HTTP_ACCEPTED = 202;
const HTTP_CONFLICT = 409;
const HTTP_INTERNAL_ERROR = 500;
const HTTP_BAD_GATEWAY = 502;
const BANK = "bank-1";
const ATTACHMENT = "012345abcdef";
const FILE_PATH = "/v1/default/files/download/export.zip";
const GRAPH_URL = `http://localhost/api/graph?bank_id=${BANK}`;

function preview(): Promise<Response> {
  return previewStrategies(
    new NextRequest("http://localhost/api/preview", { method: "POST", body: "{}" }),
    { params: Promise.resolve({ bankId: BANK }) }
  );
}

function attachment(): Promise<Response> {
  return getAttachment(new NextRequest("http://localhost/api/attachment"), {
    params: Promise.resolve({ bankId: BANK, attachmentId: ATTACHMENT }),
  });
}

function download(): Promise<Response> {
  const query = new URLSearchParams({ path: FILE_PATH });
  return downloadFile(new NextRequest(`http://localhost/api/files/download?${query}`));
}

const ROUTES = [
  { name: "POST consolidation-strategies/preview", call: preview },
  { name: "GET graph", call: () => getGraph(new NextRequest(GRAPH_URL)) },
  { name: "GET attachments/[attachmentId]", call: attachment },
  { name: "GET files/download", call: download },
];

describe("upstream relay routes", () => {
  const { fetchSpy, consoleErrorSpy } = useFetchSpies();

  describe.each(ROUTES)("$name", ({ call }) => {
    it("relays the JSON error body at the upstream status", async () => {
      const body = { detail: "Upstream rejected the request", context: { retry: false } };
      fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(body), { status: HTTP_CONFLICT }));

      const response = await call();

      expect(response.status).toBe(HTTP_CONFLICT);
      expect(await response.json()).toEqual(body);
      expect(response.headers.get("content-type")).toBe("application/json");
      expect(fetchSpy).toHaveBeenCalledOnce();
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    });

    it("relays the status text at the upstream status when the error body is not JSON", async () => {
      fetchSpy.mockResolvedValueOnce(
        new Response("<html>proxy unavailable</html>", {
          status: HTTP_BAD_GATEWAY,
          statusText: "Bad Gateway",
        })
      );

      const response = await call();

      expect(response.status).toBe(HTTP_BAD_GATEWAY);
      expect(await response.json()).toEqual({ detail: "Bad Gateway" });
      expect(response.headers.get("content-type")).toBe("application/json");
      expect(fetchSpy).toHaveBeenCalledOnce();
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    });
  });

  it("preview relays an OK JSON answer at 200", async () => {
    const body = { scopes: ["scope-1"], total: 1 };
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(body), { status: HTTP_ACCEPTED }));

    const response = await preview();

    expect(response.status).toBe(HTTP_OK);
    expect(await response.json()).toEqual(body);
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("preview catches a non-JSON OK answer and returns its own 500", async () => {
    fetchSpy.mockResolvedValueOnce(new Response("not JSON", { status: HTTP_OK }));

    const response = await preview();

    expect(response.status).toBe(HTTP_INTERNAL_ERROR);
    expect(await response.json()).toEqual({ error: "Failed to preview consolidation strategies" });
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "Error previewing consolidation strategies:",
      expect.any(Error)
    );
  });
});
