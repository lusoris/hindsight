import { proxyDataplane } from "@/lib/dataplane-proxy";

/** Proxies mental model history from the dataplane. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ bankId: string; mentalModelId: string }> }
) {
  const { bankId, mentalModelId } = await params;
  return proxyDataplane(request, {
    required: [bankId, mentalModelId],
    missing: {
      error: "bank_id and mental_model_id are required",
      errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
    },
    bankId,
    suffix: `/mental-models/${encodeURIComponent(mentalModelId)}/history`,
    method: "GET",
    failure: {
      error: "Failed to fetch mental model history",
      errorKey: "api.errors.mentalModels.history",
    },
    action: "fetching mental model history",
    notFound: {
      error: "Mental model not found",
      errorKey: "api.errors.mentalModels.notFound",
    },
    nonOkAsServerError: true,
  });
}
