import { NextRequest, NextResponse } from "next/server";
import { localizeApiErrorPayload } from "@/lib/i18n/api-errors";
import { dataplaneBankUrl, getDataplaneHeaders } from "@/lib/hindsight-client";
import { appendTagFilter, readTagFilter } from "@/lib/tag-filter-params";
import { requireQueryBankId } from "@/lib/route-validation";

export async function GET(request: NextRequest) {
  try {
    const bankId = requireQueryBankId(request);
    if (bankId instanceof NextResponse) return bankId;

    const query = new URLSearchParams();
    appendTagFilter(query, readTagFilter(request.nextUrl.searchParams));
    const response = await fetch(dataplaneBankUrl(bankId, `/knowledge-base/tree?${query}`), {
      headers: getDataplaneHeaders(),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      return NextResponse.json(error, { status: response.status });
    }
    return NextResponse.json(await response.json(), { status: 200 });
  } catch (error) {
    console.error("Failed to fetch knowledge base tree:", error);
    return NextResponse.json(
      localizeApiErrorPayload(request, {
        error: "Failed to fetch knowledge base tree",
        errorKey: "api.errors.knowledgeBase.tree",
      }),
      { status: 500 }
    );
  }
}
