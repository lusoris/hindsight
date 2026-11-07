import { NextRequest, NextResponse } from "next/server";
import { lowLevelClient, sdk } from "@/lib/hindsight-client";
import { parseJsonBody } from "@/lib/route-validation";
import { respondWithSdk } from "@/lib/sdk-response";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ bankId: string }> }
) {
  const { bankId } = await params;
  const response = await sdk.getBankConfig({
    client: lowLevelClient,
    path: { bank_id: bankId },
  });
  return respondWithSdk(response, "Failed to fetch bank config", { request });
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ bankId: string }> }
) {
  const { bankId } = await params;
  const bodyOrError = await parseJsonBody(request);
  if (bodyOrError instanceof NextResponse) return bodyOrError;
  const { updates } = bodyOrError;

  const response = await sdk.updateBankConfig({
    client: lowLevelClient,
    path: { bank_id: bankId },
    body: { updates: updates as Record<string, unknown> },
  });
  return respondWithSdk(response, "Failed to update bank config", { request });
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ bankId: string }> }
) {
  const { bankId } = await params;
  const response = await sdk.resetBankConfig({
    client: lowLevelClient,
    path: { bank_id: bankId },
  });
  return respondWithSdk(response, "Failed to reset bank config", { request });
}
