import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DataplaneProxyOptions } from "@/lib/dataplane-proxy";

const { proxyDataplane } = vi.hoisted(() => ({
  proxyDataplane: vi.fn<(request: Request, options: DataplaneProxyOptions) => Promise<Response>>(),
}));

vi.mock("@/lib/dataplane-proxy", () => ({ proxyDataplane }));

import * as directiveRoute from "@/app/api/banks/[bankId]/directives/[directiveId]/route";
import * as mentalModelRoute from "@/app/api/banks/[bankId]/mental-models/[mentalModelId]/route";
import { GET as getMentalModelHistory } from "@/app/api/banks/[bankId]/mental-models/[mentalModelId]/history/route";

type Method = "GET" | "PATCH" | "DELETE";
type Handler = (request: Request, context: { params: Promise<never> }) => Promise<unknown>;
type RouteModule = { GET: unknown; PATCH: unknown; DELETE: unknown };

// The handlers only differ in the options they hand to proxyDataplane, so this
// table is the whole contract: the dataplane path, the messages and keys the old
// hand-written handlers returned, the log labels, and the per-method flags.
const ROUTES = [
  {
    name: "directives/[directiveId]",
    module: directiveRoute,
    params: { bankId: "bank 1", directiveId: "dir/1" },
    suffix: "/directives/dir%2F1",
    missing: {
      error: "bank_id and directive_id are required",
      errorKey: "api.errors.validation.bankAndDirectiveIdRequired",
    },
    noun: "directive",
    keyNamespace: "directives",
  },
  {
    name: "mental-models/[mentalModelId]",
    module: mentalModelRoute,
    params: { bankId: "bank 1", mentalModelId: "mm/1" },
    suffix: "/mental-models/mm%2F1",
    missing: {
      error: "bank_id and mental_model_id are required",
      errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
    },
    noun: "mental model",
    keyNamespace: "mentalModels",
  },
];

const METHODS: {
  method: Method;
  /** Selects the handler by name, not by indexing the module with `method`. */
  pick: (module: RouteModule) => Handler;
  verb: string;
  participle: string;
  key: string;
  flags: Partial<DataplaneProxyOptions>;
}[] = [
  {
    method: "GET",
    pick: (module) => module.GET as Handler,
    verb: "get",
    participle: "getting",
    key: "fetch",
    flags: {},
  },
  {
    method: "PATCH",
    pick: (module) => module.PATCH as Handler,
    verb: "update",
    participle: "updating",
    key: "update",
    flags: { forwardJsonBody: true, upstreamTextAsError: true },
  },
  {
    method: "DELETE",
    pick: (module) => module.DELETE as Handler,
    verb: "delete",
    participle: "deleting",
    key: "delete",
    flags: { upstreamTextAsError: true, successBody: { success: true } },
  },
];

const CASES = ROUTES.flatMap((route) => METHODS.map((method) => ({ ...route, ...method })));

describe("bank item routes", () => {
  beforeEach(() => {
    proxyDataplane.mockReset();
    proxyDataplane.mockResolvedValue(new Response(null, { status: 200 }));
  });

  it.each(CASES)("$method $name hands proxyDataplane its options", async (testCase) => {
    const handler = testCase.pick(testCase.module);
    const request = new Request("http://localhost/api/test", { method: testCase.method });
    const ids = Object.values(testCase.params);

    await handler(request, { params: Promise.resolve(testCase.params) as Promise<never> });

    expect(proxyDataplane).toHaveBeenCalledTimes(1);
    const [forwardedRequest, options] = proxyDataplane.mock.calls[0];
    expect(forwardedRequest).toBe(request);
    expect(options).toEqual({
      required: ids,
      missing: testCase.missing,
      bankId: testCase.params.bankId,
      suffix: testCase.suffix,
      method: testCase.method,
      failure: {
        error: `Failed to ${testCase.verb} ${testCase.noun}`,
        errorKey: `api.errors.${testCase.keyNamespace}.${testCase.key}`,
      },
      action: `${testCase.participle} ${testCase.noun}`,
      ...testCase.flags,
    });
  });
});

describe("mental-models/[mentalModelId]/history route", () => {
  beforeEach(() => {
    proxyDataplane.mockReset();
    proxyDataplane.mockResolvedValue(new Response(null, { status: 200 }));
  });

  it("GET hands proxyDataplane its options", async () => {
    const request = new Request("http://localhost/api/test", { method: "GET" });
    const params = { bankId: "bank 1", mentalModelId: "mm/1" };

    await getMentalModelHistory(request, { params: Promise.resolve(params) });

    expect(proxyDataplane).toHaveBeenCalledTimes(1);
    const [forwardedRequest, options] = proxyDataplane.mock.calls[0];
    expect(forwardedRequest).toBe(request);
    expect(options).toEqual({
      required: ["bank 1", "mm/1"],
      missing: {
        error: "bank_id and mental_model_id are required",
        errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
      },
      bankId: "bank 1",
      suffix: "/mental-models/mm%2F1/history",
      method: "GET",
      failure: {
        error: "Failed to fetch mental model history",
        errorKey: "api.errors.mentalModels.history",
      },
      action: "fetching mental model history",
      notFound: {
        error: "Mental model not found",
        errorKey: "api.errors.mentalModels.notFound",
      },
      nonOkAsServerError: true,
    });
  });
});
