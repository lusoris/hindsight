import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import type { ApiFailure } from "@/lib/dataplane-proxy";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { POST as clearMentalModel } from "@/app/api/banks/[bankId]/mental-models/[mentalModelId]/clear/route";
import { POST as dryRunRefreshMentalModel } from "@/app/api/banks/[bankId]/mental-models/[mentalModelId]/dry-run-refresh/route";
import { POST as refreshMentalModel } from "@/app/api/banks/[bankId]/mental-models/[mentalModelId]/refresh/route";

// Keep the real catalogue lookup so response payloads match production, while recording
// the raw `{ error, errorKey }` payload proxyDataplane hands in before translation.
vi.mock("@/lib/i18n/api-errors", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/i18n/api-errors")>();
  return {
    ...actual,
    localizeApiErrorPayload: vi.fn(actual.localizeApiErrorPayload),
  };
});

const HTTP_OK = 200;
const HTTP_BAD_REQUEST = 400;
const HTTP_UNPROCESSABLE = 422;
const HTTP_INTERNAL_ERROR = 500;

const BANK_ID = "bank 1";
const MENTAL_MODEL_ID = "mm/1";
const TEST_URL = "http://localhost/api/banks/bank%201/mental-models/mm%2F1/action";

const MISSING_IDS_FAILURE: ApiFailure = {
  error: "bank_id and mental_model_id are required",
  errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
};

type ActionHandler = (
  request: Request,
  context: { params: Promise<{ bankId: string; mentalModelId: string }> }
) => Promise<Response>;

type ActionRouteCase = {
  name: string;
  handler: ActionHandler;
  suffix: string;
  action: string;
  failure: ApiFailure;
};

const ACTION_ROUTES: ActionRouteCase[] = [
  {
    name: "clear",
    handler: clearMentalModel,
    suffix: "/mental-models/mm%2F1/clear",
    action: "clearing mental model",
    failure: {
      error: "Failed to clear mental model",
      errorKey: "api.errors.mentalModels.clear",
    },
  },
  {
    name: "dry-run-refresh",
    handler: dryRunRefreshMentalModel,
    suffix: "/mental-models/mm%2F1/dry-run-refresh",
    action: "dry-running mental model refresh",
    failure: {
      error: "Failed to dry-run mental model refresh",
      errorKey: "api.errors.mentalModels.dryRunRefresh",
    },
  },
  {
    name: "refresh",
    handler: refreshMentalModel,
    suffix: "/mental-models/mm%2F1/refresh",
    action: "refreshing mental model",
    failure: {
      error: "Failed to refresh mental model",
      errorKey: "api.errors.mentalModels.refresh",
    },
  },
];

/** Builds a route context resolving to the given bank and mental-model ids. */
function routeContext(bankId = BANK_ID, mentalModelId = MENTAL_MODEL_ID) {
  return { params: Promise.resolve({ bankId, mentalModelId }) };
}

describe("mental-models/[mentalModelId] action routes", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(localizeApiErrorPayload).mockClear();
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    consoleErrorSpy.mockRestore();
  });

  it.each(ACTION_ROUTES)(
    "POST $name calls the encoded dataplane URL with POST and returns 200 JSON",
    async ({ handler, suffix }) => {
      const upstream = { ok: true, id: MENTAL_MODEL_ID };
      fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(upstream), { status: HTTP_OK }));

      const response = await handler(new Request(TEST_URL, { method: "POST" }), routeContext());

      expect(response.status).toBe(HTTP_OK);
      expect(await response.json()).toEqual(upstream);
      expect(fetchSpy).toHaveBeenCalledOnce();
      const [url, init] = fetchSpy.mock.calls[0];
      expect(url).toBe(dataplaneBankUrl(BANK_ID, suffix));
      expect(init).toStrictEqual({ method: "POST", headers: getDataplaneHeaders() });
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    }
  );

  it.each(
    ACTION_ROUTES.flatMap((route) => [
      { ...route, missingField: "bankId", bankId: "", mentalModelId: MENTAL_MODEL_ID },
      { ...route, missingField: "mentalModelId", bankId: BANK_ID, mentalModelId: "" },
    ])
  )(
    "POST $name returns 400 when $missingField is empty without calling upstream",
    async ({ handler, bankId, mentalModelId }) => {
      const request = new Request(TEST_URL, { method: "POST" });

      const response = await handler(request, routeContext(bankId, mentalModelId));

      expect(response.status).toBe(HTTP_BAD_REQUEST);
      expect(await response.json()).toEqual({ error: MISSING_IDS_FAILURE.error });
      expect(localizeApiErrorPayload).toHaveBeenCalledWith(request, MISSING_IDS_FAILURE);
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    }
  );

  it.each(
    ACTION_ROUTES.flatMap((route) => [
      {
        ...route,
        caseLabel: "upstream errorText",
        upstreamText: "mental model is locked",
        expectedRawError: "mental model is locked",
      },
      {
        ...route,
        caseLabel: "fallback failure error when upstream body is empty",
        upstreamText: "",
        expectedRawError: route.failure.error,
      },
    ])
  )(
    "POST $name forwards $caseLabel at the upstream 4xx status",
    async ({ handler, action, failure, upstreamText, expectedRawError }) => {
      fetchSpy.mockResolvedValueOnce(new Response(upstreamText, { status: HTTP_UNPROCESSABLE }));
      const request = new Request(TEST_URL, { method: "POST" });

      const response = await handler(request, routeContext());

      expect(response.status).toBe(HTTP_UNPROCESSABLE);
      // en.json translates `failure.errorKey`, so localizeApiErrorPayload receives `errorText`
      // while the localized response body resolves to the catalogued English string.
      expect(localizeApiErrorPayload).toHaveBeenCalledWith(request, {
        error: expectedRawError,
        errorKey: failure.errorKey,
      });
      expect(await response.json()).toEqual({ error: failure.error });
      expect(consoleErrorSpy).toHaveBeenCalledWith("API error %s:", action, upstreamText);
    }
  );

  it.each(ACTION_ROUTES)(
    "POST $name returns 500 with failure payload when fetch throws",
    async ({ handler, action, failure }) => {
      fetchSpy.mockRejectedValueOnce(new Error("upstream unreachable"));
      const request = new Request(TEST_URL, { method: "POST" });

      const response = await handler(request, routeContext());

      expect(response.status).toBe(HTTP_INTERNAL_ERROR);
      expect(localizeApiErrorPayload).toHaveBeenCalledWith(request, failure);
      expect(await response.json()).toEqual({ error: failure.error });
      expect(consoleErrorSpy).toHaveBeenCalledWith("Error %s:", action, expect.any(Error));
    }
  );

  it("POST refresh forwards { mode: 'full' } with Content-Type application/json", async () => {
    const upstream = { operation_id: "op-1" };
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify(upstream), { status: HTTP_OK }));
    const request = new Request(TEST_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "full", ignored: true }),
    });

    const response = await refreshMentalModel(request, routeContext());

    expect(response.status).toBe(HTTP_OK);
    expect(await response.json()).toEqual(upstream);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe(dataplaneBankUrl(BANK_ID, "/mental-models/mm%2F1/refresh"));
    expect(init).toStrictEqual({
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ mode: "full" }),
    });
  });

  it.each([
    { label: "no body", body: undefined },
    { label: "malformed JSON", body: "{not valid json" },
    { label: "an empty JSON object", body: "{}" },
    { label: "a non-full mode", body: JSON.stringify({ mode: "delta" }) },
    { label: "a JSON null body", body: "null" },
  ])("POST refresh sends no body when given $label", async ({ body }) => {
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: HTTP_OK }));
    const request = new Request(TEST_URL, {
      method: "POST",
      ...(body !== undefined ? { headers: { "Content-Type": "application/json" }, body } : {}),
    });

    const response = await refreshMentalModel(request, routeContext());

    expect(response.status).toBe(HTTP_OK);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe(dataplaneBankUrl(BANK_ID, "/mental-models/mm%2F1/refresh"));
    expect(init).toStrictEqual({ method: "POST", headers: getDataplaneHeaders() });
  });
});
