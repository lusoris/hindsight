import { type NextRequest, NextResponse } from "next/server";
import { isDotSegmentBankId } from "@/lib/bank-id";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";

/**
 * `null` for a usable bank id, else the 400 response: `api.errors.validation.bankIdRequired`
 * when it is empty, `api.errors.validation.bankIdDotSegment` when it is "." or ".." (a URL
 * parser would resolve it out of the bank path; see `isDotSegmentBankId`).
 */
export function validateBankId(request: Request, bankId: string | undefined): NextResponse | null {
  if (!bankId) {
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "bank_id is required",
        errorKey: "api.errors.validation.bankIdRequired",
      }),
      { status: 400 }
    );
  }
  if (isDotSegmentBankId(bankId)) {
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: 'bank_id cannot be "." or ".."',
        errorKey: "api.errors.validation.bankIdDotSegment",
      }),
      { status: 400 }
    );
  }
  return null;
}

/**
 * The bank id from the query string (first non-empty of `names`, default `["bank_id"]`),
 * or the 400 response `validateBankId` gives when none is set or the first one is "." or "..".
 */
export function requireQueryBankId(
  request: NextRequest,
  names: readonly string[] = ["bank_id"]
): string | NextResponse {
  for (const name of names) {
    const value = request.nextUrl.searchParams.get(name);
    if (value) {
      return validateBankId(request, value) ?? value;
    }
  }
  return validateBankId(request, undefined)!;
}

/** The 400 `api.errors.validation.operationIdRequired` response when `operationId` is empty, else `null`. */
export function validateOperationId(
  request: Request,
  operationId: string | undefined
): NextResponse | null {
  if (!operationId) {
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "operation_id is required",
        errorKey: "api.errors.validation.operationIdRequired",
      }),
      { status: 400 }
    );
  }
  return null;
}

/** The request body when it is a JSON object, else the 400 `api.errors.auth.invalidRequestBody` response. */
export async function parseJsonBody(
  request: Request
): Promise<NextResponse | Record<string, unknown>> {
  try {
    const body: unknown = await request.json();
    if (typeof body !== "object" || body === null || Array.isArray(body)) {
      return NextResponse.json(
        localizeApiErrorPayload(request, {
          error: "Invalid JSON body",
          errorKey: "api.errors.auth.invalidRequestBody",
        }),
        { status: 400 }
      );
    }
    return body as Record<string, unknown>;
  } catch {
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Invalid JSON body",
        errorKey: "api.errors.auth.invalidRequestBody",
      }),
      { status: 400 }
    );
  }
}
