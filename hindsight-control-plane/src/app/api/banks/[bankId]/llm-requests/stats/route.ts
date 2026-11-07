import { NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { validateBankId } from "@/lib/route-validation";

export async function GET(request: Request, { params }: { params: Promise<{ bankId: string }> }) {
  try {
    const { bankId } = await params;
    const bankIdError = validateBankId(request, bankId);
    if (bankIdError) return bankIdError;

    const { searchParams } = new URL(request.url);
    const query = searchParams.toString();

    const url = dataplaneBankUrl(bankId, `/llm-requests/stats${query ? `?${query}` : ""}`);
    const response = await fetch(url, {
      method: "GET",
      headers: getDataplaneHeaders(),
    });

    const data = await response.json();
    if (!response.ok) {
      return NextResponse.json(
        localizeApiErrorPayload(request, {
          error: data.detail || "Failed to get LLM request stats",
          errorKey: "api.errors.llmRequests.stats",
        }),
        { status: response.status }
      );
    }

    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    console.error("Error getting LLM request stats:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to get LLM request stats",
        errorKey: "api.errors.llmRequests.stats",
      }),
      { status: 500 }
    );
  }
}
