import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { respondWithBankSdk, type BankRouteContext } from "@/lib/bank-sdk-route";
import type { SdkResult } from "@/lib/sdk-response";

const HTTP_OK = 200;
const HTTP_NOT_FOUND = 404;

/** A bodyless Response with the given status, standing in for the SDK's raw response. */
function makeResponse(status: number): Response {
  return new Response(null, { status });
}

/** A successful SDK result carrying `data`. */
function ok<T>(data: T, status = HTTP_OK): SdkResult<T> {
  return { data, error: undefined, response: makeResponse(status) };
}

/** A failed SDK result carrying the upstream `error` at `status`. */
function fail(error: unknown, status: number): SdkResult<never> {
  return { data: undefined, error, response: makeResponse(status) };
}

describe("respondWithBankSdk", () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  it("awaits params, passes the bank id to call once and answers 200 with the data", async () => {
    const callSpy = vi.fn(async (bankId: string) => ok({ bankId }));
    const context: BankRouteContext = {
      params: Promise.resolve({ bankId: "bank-42" }),
    };
    const request = new Request("http://localhost/api/test");

    const response = await respondWithBankSdk(
      request,
      context,
      "Failed to process bank operation",
      callSpy
    );

    expect(callSpy).toHaveBeenCalledTimes(1);
    expect(callSpy).toHaveBeenCalledWith("bank-42");
    expect(response.status).toBe(HTTP_OK);
    await expect(response.json()).resolves.toEqual({ bankId: "bank-42" });
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it("returns 404 with failure label when upstream fails with 404", async () => {
    const upstreamError = { detail: "nope" };
    const callSpy = vi.fn(async () => fail(upstreamError, HTTP_NOT_FOUND));
    const context: BankRouteContext = {
      params: Promise.resolve({ bankId: "bank-1" }),
    };
    const request = new Request("http://localhost/api/test");
    const failureLabel = "Failed to run custom call";

    const response = await respondWithBankSdk(request, context, failureLabel, callSpy);

    expect(response.status).toBe(HTTP_NOT_FOUND);
    const body = await response.json();
    expect(body.error).toBe(failureLabel);
    expect(body.details).toBe("nope");
    expect(body.upstream).toEqual({
      status: HTTP_NOT_FOUND,
      detail: upstreamError,
    });
    expect(consoleErrorSpy).toHaveBeenCalledWith(`${failureLabel}:`, {
      upstreamStatus: HTTP_NOT_FOUND,
      upstreamError,
    });
  });
});
