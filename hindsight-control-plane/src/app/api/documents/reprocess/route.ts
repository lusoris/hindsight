import { NextRequest, NextResponse } from "next/server";
import { relayUpstreamJson } from "@/lib/dataplane-proxy";
import { localizeApiErrorPayload, missingQueryParam } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { validateBankId } from "@/lib/route-validation";

export async function POST(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const bankId = searchParams.get("bank_id");
    const documentId = searchParams.get("document_id");

    if (!bankId) return missingQueryParam(request, "bank_id");
    const bankIdError = validateBankId(request, bankId);
    if (bankIdError) return bankIdError;
    if (!documentId) return missingQueryParam(request, "document_id");

    const response = await fetch(
      dataplaneBankUrl(bankId, `/documents/${encodeURIComponent(documentId)}/reprocess`),
      {
        method: "POST",
        headers: getDataplaneHeaders({ "Content-Type": "application/json" }),
      }
    );

    return await relayUpstreamJson(response, 200);
  } catch (error) {
    console.error("Error reprocessing document:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to reprocess document",
        errorKey: "api.errors.documents.reprocess",
      }),
      { status: 500 }
    );
  }
}
