import type {
  BuyerLeadTier,
  BuyerRelationPair,
  CandidateMember,
} from "./types";
import type { Tone } from "./formatters";

export const buyerLeadTierOrder: readonly BuyerLeadTier[] = [
  "HIGH",
  "CHANGED",
  "RELATED",
  "SAME_CATEGORY",
  "UNKNOWN",
];

const tierPresentations: Record<
  BuyerLeadTier,
  { label: string; reason: string; tone: Tone }
> = {
  HIGH: {
    label: "强潜客",
    reason: "类目方向存在明确差异，建议优先联系",
    tone: "danger",
  },
  CHANGED: {
    label: "变化潜客",
    reason: "类目方向出现变化迹象，建议进一步沟通",
    tone: "warning",
  },
  RELATED: {
    label: "相关潜客",
    reason: "当前方向与原方向相关，可能存在同赛道扩张或转型需求",
    tone: "processing",
  },
  SAME_CATEGORY: {
    label: "同类潜客",
    reason: "当前方向与原方向一致，仍可能存在同赛道买号、矩阵扩张或换号需求",
    tone: "default",
  },
  UNKNOWN: {
    label: "待判断",
    reason: "当前资料不足，暂无法可靠判断",
    tone: "default",
  },
};

export function buyerLeadTierPresentation(
  tier: BuyerLeadTier | null | undefined,
) {
  return tier ? tierPresentations[tier] : tierPresentations.UNKNOWN;
}

const relationLabels: Record<string, string> = {
  EXACT: "同类目",
  PARENT_CHILD: "上下级/细分类目关系",
  COMPATIBLE: "相关类目",
  INCOMPATIBLE: "明确跨类目",
  NO_RULE: "暂无明确关系规则",
};

export function buyerRelationLabel(relation: string) {
  return relationLabels[relation] ?? "暂无明确关系规则";
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

export function buyerOriginalCategories(member: CandidateMember): string[] {
  return stringList(member.buyer_relation_summary?.client_categories);
}

export function buyerCurrentCategories(member: CandidateMember): string[] {
  return stringList(member.buyer_relation_summary?.creator_categories);
}

export function buyerRelationPairs(
  member: CandidateMember,
): BuyerRelationPair[] {
  const pairs = member.buyer_relation_summary?.pairs;
  return Array.isArray(pairs)
    ? pairs.filter(
        (pair): pair is BuyerRelationPair =>
          Boolean(pair) &&
          typeof pair.client_category_id === "string" &&
          typeof pair.creator_category_id === "string" &&
          typeof pair.relation === "string",
      )
    : [];
}

export function buyerRunHasTierData(
  summary: Record<BuyerLeadTier, CandidateMember[]> | undefined,
): boolean {
  return Boolean(
    summary && buyerLeadTierOrder.some((tier) => summary[tier].length),
  );
}
