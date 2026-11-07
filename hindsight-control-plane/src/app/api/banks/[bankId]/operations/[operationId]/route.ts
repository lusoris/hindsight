import { NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { sdk, lowLevelClient, dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { validateBankId, validateOperationId } from "@/lib/route-validation";
import { respondWithSdk } from "@/lib/sdk-response";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ bankId: string; operationId: string }> }
) {
  const { bankId, operationId } = await params;
  const bankIdError = validateBankId(request, bankId);
  if (bankIdError) return bankIdError;
  const opIdError = validateOperationId(request, operationId);
  if (opIdError) return opIdError;

  const url = new URL(request.url);
  const includePayload = url.searchParams.get("include_payload") === "true";

  const response = await sdk.getOperationStatus({
    client: lowLevelClient,
    path: { bank_id: bankId, operation_id: operationId },
    query: includePayload ? { include_payload: true } : undefined,
  });
  return respondWithSdk(response, "Failed to get operation status", { request });
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ bankId: string; operationId: string }> }
) {
  try {
    const { bankId, operationId } = await params;
    const bankIdError = validateBankId(request, bankId);
    if (bankIdError) return bankIdError;
    const opIdError = validateOperationId(request, operationId);
    if (opIdError) return opIdError;

    const url = dataplaneBankUrl(bankId, `/operations/${encodeURIComponent(operationId)}/retry`);
    const response = await fetch(url, {
      method: "POST",
      headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
    });

    const data = await response.json();

    if (!response.ok) {
      return NextResponse.json(
        localizeApiErrorPayload(request, {
          error: data.detail || "Failed to retry operation",
          errorKey: "api.errors.operations.retry",
        }),
        { status: response.status }
      );
    }

    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error retrying operation:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to retry operation",
        errorKey: "api.errors.operations.retry",
      }),
      { status: 500 }
    );
  }
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ bankId: string; operationId: string }> }
) {
  try {
    const { bankId, operationId } = await params;
    const bankIdError = validateBankId(request, bankId);
    if (bankIdError) return bankIdError;
    const opIdError = validateOperationId(request, operationId);
    if (opIdError) return opIdError;

    const response = await sdk.deleteOperation({
      client: lowLevelClient,
      path: { bank_id: bankId, operation_id: operationId },
    });
    return respondWithSdk(response, "Failed to delete operation", { request });
  } catch (error) {
    console.error("Error deleting operation:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to delete operation",
        errorKey: "api.errors.operations.delete",
      }),
      { status: 500 }
    );
  }
}
