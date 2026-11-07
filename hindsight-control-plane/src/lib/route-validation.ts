import { NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";

export function validateBankId(
  request: Request,
  bankId: string | undefined,
): NextResponse | null {
  if (!bankId) {
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "bank_id is required",
        errorKey: "api.errors.validation.bankIdRequired",
      }),
      { status: 400 },
    );
  }
  return null;
}

export function validateOperationId(
  request: Request,
  operationId: string | undefined,
): NextResponse | null {
  if (!operationId) {
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "operation_id is required",
        errorKey: "api.errors.validation.operationIdRequired",
      }),
      { status: 400 },
    );
  }
  return null;
}

export async function parseJsonBody(
  request: Request,
): Promise<NextResponse | Record<string, unknown>> {
  try {
    return (await request.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Invalid JSON body",
        errorKey: "api.errors.auth.invalidRequestBody",
      }),
      { status: 400 },
    );
  }
}
