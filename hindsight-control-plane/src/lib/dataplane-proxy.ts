import { NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import type { BankRouteContext } from "@/lib/bank-sdk-route";
import { validateBankId } from "@/lib/route-validation";

export type ApiFailure = { error: string; errorKey: string };

/** Route context of a `/banks/[bankId]/mental-models/[mentalModelId]/...` handler. */
export type MentalModelRouteContext = {
  params: Promise<{ bankId: string; mentalModelId: string }>;
};

export type DataplaneProxyOptions = {
  /** Every id the route requires; any falsy entry returns 400 with `missing`. */
  required: readonly (string | undefined)[];
  missing: ApiFailure;
  bankId: string;
  /** Path under the bank, e.g. `/directives/${encodeURIComponent(id)}`. */
  suffix: string;
  method: "GET" | "POST" | "PATCH" | "DELETE";
  /** Payload for a non-OK upstream (at the upstream status) and for a thrown error (at 500). */
  failure: ApiFailure;
  /** Log label, e.g. "getting directive": logs read `API error <action>:` and `Error <action>:`. */
  action: string;
  /** PATCH: parse the request JSON body and forward it with Content-Type application/json. */
  forwardJsonBody?: boolean;
  /** POST: serialize this value and send it with Content-Type application/json when not undefined. */
  jsonBody?: unknown;
  /** POST/PATCH/DELETE: a non-OK upstream reports `upstreamText || failure.error` as `error`. */
  upstreamTextAsError?: boolean;
  /** DELETE: body for a 200 instead of the upstream JSON (today `{ success: true }`). */
  successBody?: unknown;
  /** An upstream 404 returns this payload at 404, without reading or logging the upstream body. */
  notFound?: ApiFailure;
  /** A non-OK upstream (other than a `notFound` 404) answers `failure` at 500 instead of at the upstream status. */
  nonOkAsServerError?: boolean;
};

/** What a route handler supplies per method; the route fills in the ids and the path. */
export type DataplaneCall = Omit<
  DataplaneProxyOptions,
  "required" | "missing" | "bankId" | "suffix"
>;

/**
 * Proxies one bank-scoped dataplane call: 400 with `missing` when an id is absent, the upstream
 * JSON (or `successBody`) at 200, `failure` at the upstream status or at 500 when anything throws.
 */
export async function proxyDataplane(
  request: Request,
  options: DataplaneProxyOptions
): Promise<NextResponse> {
  const { action, failure } = options;
  try {
    for (const id of options.required) {
      if (!id) {
        return NextResponse.json(localizeApiErrorPayload(request, options.missing), {
          status: 400,
        });
      }
    }

    const hasJsonBody = Boolean(options.forwardJsonBody) || options.jsonBody !== undefined;
    const init: RequestInit = {
      method: options.method,
      headers: hasJsonBody
        ? getDataplaneHeaders({ "Content-Type": "application/json" })
        : getDataplaneHeaders(),
    };

    if (options.forwardJsonBody) {
      const body = await request.json();
      init.body = JSON.stringify(body);
    } else if (options.jsonBody !== undefined) {
      init.body = JSON.stringify(options.jsonBody);
    }

    const response = await fetch(dataplaneBankUrl(options.bankId, options.suffix), init);

    if (!response.ok) {
      if (options.notFound && response.status === 404) {
        return NextResponse.json(localizeApiErrorPayload(request, options.notFound), {
          status: 404,
        });
      }
      if (options.nonOkAsServerError) {
        // The catch below turns it into the 500.
        throw new Error(`API returned ${response.status}`);
      }
      const errorText = await response.text();
      // Constant format string: `action` goes in as a %s argument, so a `%` in it cannot
      // act as a format specifier. The printed line is the same as the old interpolation.
      console.error("API error %s:", action, errorText);
      return NextResponse.json(
        localizeApiErrorPayload(request, {
          error: options.upstreamTextAsError ? errorText || failure.error : failure.error,
          errorKey: failure.errorKey,
        }),
        { status: response.status }
      );
    }

    const data = options.successBody !== undefined ? options.successBody : await response.json();
    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error %s:", action, error);
    return NextResponse.json(localizeApiErrorPayload(request, failure), {
      status: 500,
    });
  }
}

/** Relays an upstream error's JSON, or `{ detail: statusText }` for non-JSON, at its own status. */
export async function relayUpstreamError(response: Response): Promise<NextResponse> {
  const error = await response.json().catch(() => ({ detail: response.statusText }));
  return NextResponse.json(error, { status: response.status });
}

/**
 * Answers with what the dataplane answered: its JSON at `successStatus` when the call succeeded,
 * otherwise its error body (or `{ detail: statusText }` when that is not JSON) at its own status.
 * An OK answer that is not JSON throws, for the caller's catch to turn into its 500.
 */
export async function relayUpstreamJson(
  response: Response,
  successStatus: number
): Promise<NextResponse> {
  if (!response.ok) {
    return relayUpstreamError(response);
  }
  return NextResponse.json(await response.json(), { status: successStatus });
}

export type BankGetRelayOptions = {
  /** Path under the bank, including the route's forwarded query. */
  suffix: (searchParams: URLSearchParams) => string;
  failure: ApiFailure;
  /** Log label used in `Error <action>:` and `API error <action>:`. */
  action: string;
  onUpstreamError: "detail" | "log" | "relay";
};

/**
 * Validates the bank id and relays a GET with route-specific query forwarding. Success JSON
 * answers at 200; upstream errors use their status and either a localized detail, a logged
 * text body with the localized failure, or the unchanged JSON/status-text fallback. Anything
 * that throws answers the localized failure at 500 and logs the route's action.
 */
export async function relayBankGet(
  request: Request,
  context: BankRouteContext,
  options: BankGetRelayOptions
): Promise<NextResponse> {
  const { action, failure } = options;
  try {
    const { bankId } = await context.params;
    const bankIdError = validateBankId(request, bankId);
    if (bankIdError) return bankIdError;

    const suffix = options.suffix(new URL(request.url).searchParams);
    // nosemgrep: rules_lgpl_javascript_ssrf_rule-node-ssrf -- host is the server-set DATAPLANE_URL and the request adds only the encoded bank id and allow-listed query values, the flow main ran in the four routes this replaces; "." and "..", which a URL parser resolves out of the bank path, get a 400 from validateBankId and a RangeError from dataplaneBankUrl (tests/lib/dataplane-proxy.test.ts, tests/lib/hindsight-client.test.ts); category D, expires 2027-03-20, ends when this fetch moves to the generated SDK client
    const response = await fetch(dataplaneBankUrl(bankId, suffix), {
      method: "GET",
      headers: getDataplaneHeaders(),
    });

    if (options.onUpstreamError === "detail") {
      // Audit/LLM routes parse before checking ok: even a non-OK non-JSON body must reach 500.
      const data = await response.json();
      return NextResponse.json(
        response.ok
          ? data
          : localizeApiErrorPayload(request, {
              error: data.detail || failure.error,
              errorKey: failure.errorKey,
            }),
        { status: response.ok ? 200 : response.status }
      );
    }

    if (options.onUpstreamError === "log" && !response.ok) {
      const text = await response.text();
      console.error("API error %s:", action, text);
      return NextResponse.json(localizeApiErrorPayload(request, failure), {
        status: response.status,
      });
    }

    return await relayUpstreamJson(response, 200);
  } catch (error) {
    console.error("Error %s:", action, error);
    return NextResponse.json(localizeApiErrorPayload(request, failure), { status: 500 });
  }
}

/** One dataplane POST a bank route relays: the path under the bank (query included) and its JSON body. */
export type BankPostCall = {
  /** Path under the bank, e.g. `/import?dry_run=true`. */
  suffix: string;
  /** Sent as the request body with Content-Type application/json when not undefined. */
  jsonBody?: unknown;
};

type BankPostRelayOptions = {
  /** Status of the relayed upstream JSON when the upstream call succeeds. */
  successStatus: number;
  /** Payload at 500 when reading the request, the call or the upstream answer throws. */
  failure: ApiFailure;
  /** Log label, e.g. "cloning bank": a thrown error logs `Error <action>:`. */
  action: string;
};

/**
 * Relays one bank-scoped dataplane POST whose answer the UI reads as is: `prepare` reads the
 * request and names the call (or answers early, e.g. 400), the upstream JSON comes back at
 * `successStatus`, and an upstream error body (or `{ detail: statusText }` when it is not JSON)
 * at the upstream status. Anything that throws, `prepare` included, answers `failure` at 500.
 *
 * The clone and import routes each carried a copy of this before and disagreed on non-JSON
 * answers: clone relayed any of them as `{ detail: statusText }` (an OK one at 202), import
 * turned every one into its 500. Both now follow `relayUpstreamJson`, the rule the knowledge-base
 * proxy already used; both dataplane endpoints answer JSON models, so only a broken upstream
 * reaches either branch.
 */
export async function relayBankPost(
  request: Request,
  context: BankRouteContext,
  options: BankPostRelayOptions,
  prepare: (bankId: string) => Promise<BankPostCall | NextResponse>
): Promise<NextResponse> {
  try {
    const { bankId } = await context.params;
    const call = await prepare(bankId);
    if (call instanceof NextResponse) return call;

    const init: RequestInit =
      call.jsonBody === undefined
        ? { method: "POST", headers: getDataplaneHeaders() }
        : {
            method: "POST",
            headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(call.jsonBody),
          };
    const response = await fetch(dataplaneBankUrl(bankId, call.suffix), init);
    return await relayUpstreamJson(response, options.successStatus);
  } catch (error) {
    // Constant format string: `action` goes in as a %s argument, so a `%` in it cannot
    // act as a format specifier.
    console.error("Error %s:", options.action, error);
    return NextResponse.json(localizeApiErrorPayload(request, options.failure), { status: 500 });
  }
}
