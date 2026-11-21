import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { hindsightClient } from "@/lib/hindsight-client";
import { validateBankId } from "@/lib/route-validation";

export async function POST(request: NextRequest) {
  // The body is read in its own `try`, ahead of the bank id check, so the `catch` that relays an
  // SDK error's status runs only after the check: one `try` around both let that relay answer
  // before the check with any status the caught value carried. A body that fails to read has no
  // `statusCode`, so it gets the same 500 as before.
  let body;
  let bankId;
  try {
    body = await request.json();
    bankId = body.bank_id || body.agent_id;
  } catch (error: any) {
    console.error("Error batch retain:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: error?.message || String(error) || "Failed to batch retain",
        errorKey: "api.errors.memories.retain",
      }),
      { status: 500 }
    );
  }

  const bankIdError = validateBankId(request, bankId);
  if (bankIdError) return bankIdError;

  try {
    const { items, document_id, document_tags, observation_scopes } = body;

    // Map observation_scopes into each item if provided at request level
    const mappedItems = observation_scopes
      ? items?.map((item: any) => ({
          ...item,
          observation_scopes: item.observation_scopes ?? observation_scopes,
        }))
      : items;

    const response = await hindsightClient.retainBatch(bankId, mappedItems, {
      documentId: document_id,
      documentTags: document_tags,
    });

    return NextResponse.json(response, { status: 200 });
  } catch (error: any) {
    console.error("Error batch retain:", error);

    const errorMessage = error?.message || String(error);
    const errorDetails = error?.details;
    const statusCode = error?.statusCode;

    // If we have a statusCode, use it
    if (statusCode && typeof statusCode === "number") {
      return NextResponse.json(
        localizeApiErrorPayload(request, {
          error: errorMessage,
          details: errorDetails,
          errorKey: "api.errors.memories.retain",
        }),
        { status: statusCode }
      );
    }

    // Otherwise, return generic 500 error
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: errorMessage || "Failed to batch retain",
        errorKey: "api.errors.memories.retain",
      }),
      { status: 500 }
    );
  }
}
