import type { NextResponse } from "next/server";
import type { RequestLike } from "@/lib/i18n/api-errors";
import { validateBankId } from "@/lib/route-validation";
import { respondWithSdk, type SdkResult } from "@/lib/sdk-response";

/** Route context of a `/banks/[bankId]/...` handler. */
export type BankRouteContext = { params: Promise<{ bankId: string }> };

/**
 * Awaits the bank id from the route params, answers `validateBankId`'s 400 for an empty, "." or
 * ".." id, else runs one bank-scoped SDK call and serializes it with `respondWithSdk` (same
 * failure label, `{ request }` for localization). The generated path serializer leaves "." and
 * ".." as they are, so without the check a URL parser would resolve them out of the bank path.
 */
export async function respondWithBankSdk<T>(
  request: RequestLike,
  context: BankRouteContext,
  failureLabel: string,
  call: (bankId: string) => Promise<SdkResult<T>>
): Promise<NextResponse> {
  const { bankId } = await context.params;
  const bankIdError = validateBankId(request, bankId);
  if (bankIdError) return bankIdError;
  return respondWithSdk(await call(bankId), failureLabel, { request });
}
