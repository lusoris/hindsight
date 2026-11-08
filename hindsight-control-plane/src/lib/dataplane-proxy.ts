import { NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";

export type ApiFailure = { error: string; errorKey: string };

export type DataplaneProxyOptions = {
  /** Every id the route requires; any falsy entry returns 400 with `missing`. */
  required: readonly (string | undefined)[];
  missing: ApiFailure;
  bankId: string;
  /** Path under the bank, e.g. `/directives/${encodeURIComponent(id)}`. */
  suffix: string;
  method: "GET" | "PATCH" | "DELETE";
  /** Payload for a non-OK upstream (at the upstream status) and for a thrown error (at 500). */
  failure: ApiFailure;
  /** Log label, e.g. "getting directive": logs read `API error <action>:` and `Error <action>:`. */
  action: string;
  /** PATCH: parse the request JSON body and forward it with Content-Type application/json. */
  forwardJsonBody?: boolean;
  /** PATCH/DELETE: a non-OK upstream reports `upstreamText || failure.error` as `error`. */
  upstreamTextAsError?: boolean;
  /** DELETE: body for a 200 instead of the upstream JSON (today `{ success: true }`). */
  successBody?: unknown;
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

    const init: RequestInit = {
      method: options.method,
      headers: options.forwardJsonBody
        ? getDataplaneHeaders({ "Content-Type": "application/json" })
        : getDataplaneHeaders(),
    };

    if (options.forwardJsonBody) {
      const body = await request.json();
      init.body = JSON.stringify(body);
    }

    const response = await fetch(dataplaneBankUrl(options.bankId, options.suffix), init);

    if (!response.ok) {
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
