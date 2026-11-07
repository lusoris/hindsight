import { NextResponse } from "next/server";
import { sdk, lowLevelClient } from "@/lib/hindsight-client";
import { parseJsonBody, validateBankId } from "@/lib/route-validation";
import { respondWithSdk } from "@/lib/sdk-response";

export async function PUT(request: Request, { params }: { params: Promise<{ bankId: string }> }) {
  const { bankId } = await params;
  const bankIdError = validateBankId(request, bankId);
  if (bankIdError) return bankIdError;

  const bodyOrError = await parseJsonBody(request);
  if (bodyOrError instanceof NextResponse) return bodyOrError;
  const body = bodyOrError;

  const response = await sdk.createOrUpdateBank({
    client: lowLevelClient,
    path: { bank_id: bankId },
    body: {
      name: body.name,
      mission: body.mission,
      disposition: body.disposition,
    },
  });
  return respondWithSdk(response, "Failed to update bank", { request });
}

export async function PATCH(request: Request, { params }: { params: Promise<{ bankId: string }> }) {
  const { bankId } = await params;
  const bankIdError = validateBankId(request, bankId);
  if (bankIdError) return bankIdError;

  const bodyOrError = await parseJsonBody(request);
  if (bodyOrError instanceof NextResponse) return bodyOrError;
  const body = bodyOrError;

  const response = await sdk.updateBank({
    client: lowLevelClient,
    path: { bank_id: bankId },
    body: {
      name: body.name,
      mission: body.mission,
      disposition: body.disposition,
    },
  });
  return respondWithSdk(response, "Failed to update bank", { request });
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ bankId: string }> }
) {
  const { bankId } = await params;

  const bankIdError = validateBankId(request, bankId);
  if (bankIdError) return bankIdError;

  const response = await sdk.deleteBank({
    client: lowLevelClient,
    path: { bank_id: bankId },
  });
  return respondWithSdk(response, "Failed to delete bank", { request });
}
