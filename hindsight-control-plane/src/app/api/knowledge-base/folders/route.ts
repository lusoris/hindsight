import { NextRequest } from "next/server";
import { createKnowledgeBaseNode } from "@/lib/knowledge-base-proxy";

/** Creates a knowledge-base folder in the bank from the JSON body. */
export async function POST(request: NextRequest) {
  return createKnowledgeBaseNode(request, "folders", {
    error: "Failed to create folder",
    errorKey: "api.errors.knowledgeBase.createFolder",
  });
}
