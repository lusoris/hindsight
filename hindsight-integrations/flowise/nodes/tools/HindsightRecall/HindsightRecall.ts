import { DynamicStructuredTool } from "@langchain/core/tools";
import { z } from "zod";

import { ICommonObject, INodeData } from "../../../src/Interface";
import * as common from "../HindsightCommon/core";

const RecallSchema = z.object({
  bankId: z.string().describe("The Hindsight memory bank to search."),
  query: z.string().describe("Natural-language query describing what to recall."),
  budget: z
    .enum(["low", "mid", "high"])
    .optional()
    .describe("Recall budget: low/mid/high. Higher values search more memories at higher cost."),
  maxTokens: z
    .number()
    .int()
    .positive()
    .optional()
    .describe("Maximum tokens to return in the recall response."),
  tags: z.array(z.string()).optional().describe("Optional list of tags to filter the search by."),
});

class HindsightRecall_Tools extends common.HindsightToolNode {
  constructor() {
    super({
      label: "Hindsight Recall",
      name: "hindsightRecall",
      type: "HindsightRecall",
      inputs: [common.defaultBankInput("recall from"), common.defaultBudgetInput("recall")],
      description:
        "Search a Hindsight memory bank for memories relevant to a query. Returns ranked results.",
    });
  }

  async init(nodeData: INodeData, _: string, options: ICommonObject): Promise<unknown> {
    const { client, defaultBankId, defaultBudget } = await common.createToolContext(
      nodeData,
      options
    );

    return new DynamicStructuredTool({
      name: "hindsight_recall",
      description:
        "Search a Hindsight memory bank with a natural-language query and return the most relevant memories. Use this before answering the user to ground your response in past context.",
      schema: RecallSchema as any,
      func: async ({
        bankId,
        query,
        budget,
        maxTokens,
        tags,
      }: {
        bankId: string;
        query: string;
        budget?: common.Budget;
        maxTokens?: number;
        tags?: string[];
      }) => {
        const targetBank = bankId || defaultBankId;
        if (!targetBank) {
          return "Error: bankId is required (no default bank configured on the node).";
        }
        const opts: Record<string, unknown> = {
          budget: budget || defaultBudget,
        };
        if (typeof maxTokens === "number") opts.maxTokens = maxTokens;
        if (tags && tags.length) opts.tags = tags;
        const response = await client.recall(targetBank, query, opts as any);
        return JSON.stringify(response);
      },
    });
  }
}

module.exports = { nodeClass: HindsightRecall_Tools };
