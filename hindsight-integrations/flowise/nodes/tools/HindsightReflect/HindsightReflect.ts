import { DynamicStructuredTool } from "@langchain/core/tools";
import { z } from "zod";

import { ICommonObject, INodeData } from "../../../src/Interface";
import * as common from "../HindsightCommon/core";

const ReflectSchema = z.object({
  bankId: z.string().describe("The Hindsight memory bank to reflect on."),
  query: z.string().describe("Question to answer using the bank."),
  budget: z
    .enum(["low", "mid", "high"])
    .optional()
    .describe("Reflect budget: low/mid/high. Higher values consider more memories at higher cost."),
});

class HindsightReflect_Tools extends common.HindsightToolNode {
  constructor() {
    super({
      label: "Hindsight Reflect",
      name: "hindsightReflect",
      type: "HindsightReflect",
      inputs: [common.defaultBankInput("reflect on"), common.defaultBudgetInput("reflect")],
      description:
        'Get an LLM-synthesized answer over a Hindsight memory bank. Useful for "what do we know about X?" style questions.',
    });
  }

  async init(nodeData: INodeData, _: string, options: ICommonObject): Promise<unknown> {
    const { client, defaultBankId, defaultBudget } = await common.createToolContext(
      nodeData,
      options
    );

    return new DynamicStructuredTool({
      name: "hindsight_reflect",
      description:
        'Ask a question over a Hindsight memory bank and get an LLM-synthesized answer that pulls from many memories. Use for open-ended "what do we know about X?" or "what should we do?" questions.',
      schema: ReflectSchema as any,
      func: async ({
        bankId,
        query,
        budget,
      }: {
        bankId: string;
        query: string;
        budget?: common.Budget;
      }) => {
        const targetBank = bankId || defaultBankId;
        if (!targetBank) {
          return "Error: bankId is required (no default bank configured on the node).";
        }
        const response = await client.reflect(targetBank, query, {
          budget: budget || defaultBudget,
        });
        return JSON.stringify(response);
      },
    });
  }
}

module.exports = { nodeClass: HindsightReflect_Tools };
