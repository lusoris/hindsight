import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { sdk, lowLevelClient } from "@/lib/hindsight-client";
import { respondWithSdk } from "@/lib/sdk-response";
import { validateBankId } from "@/lib/route-validation";

export async function POST(request: NextRequest) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Invalid JSON body",
        errorKey: "api.errors.auth.invalidRequestBody",
      }),
      { status: 400 }
    );
  }
  const bankId = body.bank_id || body.agent_id;

  const bankIdError = validateBankId(request, bankId);
  if (bankIdError) return bankIdError;

  const { items } = body;

  const response = await sdk.retainMemories({
    client: lowLevelClient,
    path: { bank_id: bankId },
    body: { items, async: true },
  });
  return respondWithSdk(response, "Failed to batch retain async", { request });
}
