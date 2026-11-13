import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { POST as cloneRoute } from "@/app/api/banks/[bankId]/clone/route";
import { POST as importRoute } from "@/app/api/banks/[bankId]/import/route";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { translateApiError } from "@/lib/i18n/api-errors";
import { useFetchSpies } from "../../../lib/fetch-spies";

const HTTP_OK = 200;
const HTTP_ACCEPTED = 202;
const HTTP_BAD_REQUEST = 400;
const HTTP_INTERNAL_ERROR = 500;

const BANK_ID = "bank 1";
const CONTEXT = { params: Promise.resolve({ bankId: BANK_ID }) };
const BASE_URL = "http://localhost/api/banks/bank%201";

/** A POST to the control-plane route at `path` carrying the raw `body`. */
function post(path: string, body: string): NextRequest {
  return new NextRequest(`${BASE_URL}${path}`, { method: "POST", body });
}

describe("bank clone and import routes", () => {
  const { fetchSpy, consoleErrorSpy } = useFetchSpies();

  /** Makes the next dataplane call answer `body` as JSON at `status`. */
  function upstreamReturns(body: unknown, status: number): void {
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(body), { status }));
  }

  /** Asserts exactly one dataplane call, to `suffix` under BANK_ID with `init`. */
  function expectDataplaneCall(suffix: string, init: RequestInit): void {
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [calledUrl, calledInit] = fetchSpy.mock.calls[0];
    expect(calledUrl).toBe(dataplaneBankUrl(BANK_ID, suffix));
    expect(calledInit).toStrictEqual(init);
  }

  describe("POST clone", () => {
    it.each([
      ["has no target_bank_id", JSON.stringify({ include_data: true })],
      ["has a non-string target_bank_id", JSON.stringify({ target_bank_id: 7 })],
      ["is not JSON", "not json"],
    ])("answers 400 without calling the dataplane when the body %s", async (_case, body) => {
      const response = await cloneRoute(post("/clone", body), CONTEXT);

      expect(response.status).toBe(HTTP_BAD_REQUEST);
      expect(await response.json()).toEqual({
        error: translateApiError("api.errors.validation.targetBankIdRequired"),
      });
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("forwards the target and only the boolean scope flags, and answers 202", async () => {
      const operation = { operation_id: "op-1", status: "pending" };
      upstreamReturns(operation, HTTP_ACCEPTED);
      const body = {
        target_bank_id: "copy of bank",
        include_data: false,
        include_bank_config: true,
        include_history: "yes",
      };

      const response = await cloneRoute(post("/clone", JSON.stringify(body)), CONTEXT);

      expect(response.status).toBe(HTTP_ACCEPTED);
      expect(await response.json()).toEqual(operation);
      expectDataplaneCall(
        "/clone?target_bank_id=copy+of+bank&include_data=false&include_bank_config=true",
        { method: "POST", headers: getDataplaneHeaders() }
      );
    });

    it("answers the localized clone failure at 500 when the dataplane is unreachable", async () => {
      fetchSpy.mockRejectedValueOnce(new Error("connection refused"));

      const response = await cloneRoute(
        post("/clone", JSON.stringify({ target_bank_id: "copy" })),
        CONTEXT
      );

      expect(response.status).toBe(HTTP_INTERNAL_ERROR);
      expect(await response.json()).toEqual({ error: translateApiError("api.errors.banks.clone") });
      expect(consoleErrorSpy).toHaveBeenCalledWith("Error %s:", "cloning bank", expect.any(Error));
    });
  });

  describe("POST import", () => {
    const MANIFEST = { version: "1", bank: { name: "imported" } };

    it.each([
      ["with ?dry_run=true", "/import?dry_run=true", "/import?dry_run=true"],
      ["without dry_run", "/import", "/import"],
      ["with ?dry_run=false", "/import?dry_run=false", "/import"],
    ])(
      "forwards the manifest %s and answers the upstream JSON at 200",
      async (_c, path, suffix) => {
        const result = { applied: true };
        upstreamReturns(result, HTTP_OK);

        const response = await importRoute(post(path, JSON.stringify(MANIFEST)), CONTEXT);

        expect(response.status).toBe(HTTP_OK);
        expect(await response.json()).toEqual(result);
        expectDataplaneCall(suffix, {
          method: "POST",
          headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify(MANIFEST),
        });
      }
    );

    it("answers the localized import failure at 500 when the manifest is not JSON", async () => {
      const response = await importRoute(post("/import", "not json"), CONTEXT);

      expect(response.status).toBe(HTTP_INTERNAL_ERROR);
      expect(await response.json()).toEqual({
        error: translateApiError("api.errors.banks.importTemplate"),
      });
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(consoleErrorSpy).toHaveBeenCalledWith(
        "Error %s:",
        "importing bank template",
        expect.any(Error)
      );
    });
  });
});
