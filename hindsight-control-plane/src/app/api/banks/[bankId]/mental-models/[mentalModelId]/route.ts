import { proxyDataplane, type ApiFailure, type DataplaneCall } from "@/lib/dataplane-proxy";

const MISSING_PAYLOAD: ApiFailure = {
  error: "bank_id and mental_model_id are required",
  errorKey: "api.errors.validation.bankAndMentalModelIdRequired",
};

type HandlerContext = { params: Promise<{ bankId: string; mentalModelId: string }> };

/**
 * Resolves the ids and the dataplane path once for GET, PATCH and DELETE, which differ
 * only in the call options.
 */
async function proxyMentalModel(request: Request, { params }: HandlerContext, call: DataplaneCall) {
  const { bankId, mentalModelId } = await params;
  return proxyDataplane(request, {
    required: [bankId, mentalModelId],
    missing: MISSING_PAYLOAD,
    bankId,
    suffix: `/mental-models/${encodeURIComponent(mentalModelId)}`,
    ...call,
  });
}

/** Proxies one mental model from the dataplane. */
export async function GET(request: Request, context: HandlerContext) {
  return proxyMentalModel(request, context, {
    method: "GET",
    failure: { error: "Failed to get mental model", errorKey: "api.errors.mentalModels.fetch" },
    action: "getting mental model",
  });
}

/** Forwards a mental model update to the dataplane. */
export async function PATCH(request: Request, context: HandlerContext) {
  return proxyMentalModel(request, context, {
    method: "PATCH",
    failure: { error: "Failed to update mental model", errorKey: "api.errors.mentalModels.update" },
    action: "updating mental model",
    forwardJsonBody: true,
    upstreamTextAsError: true,
  });
}

/** Deletes one mental model on the dataplane. */
export async function DELETE(request: Request, context: HandlerContext) {
  return proxyMentalModel(request, context, {
    method: "DELETE",
    failure: { error: "Failed to delete mental model", errorKey: "api.errors.mentalModels.delete" },
    action: "deleting mental model",
    upstreamTextAsError: true,
    successBody: { success: true },
  });
}
