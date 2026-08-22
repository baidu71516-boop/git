import type { InfluencerListItem } from "@/features/influencers/types";

import type { Campaign, CampaignMember, CampaignMemberAddItem } from "./types";

export type CampaignMemberPreviewState = "ready" | "empty" | "error";

export type CampaignMemberPreviewSubmitOutcome = "success" | "account-error";

export type CampaignMemberPreviewRemoveOutcome =
  "success" | "conflict-still-present" | "conflict-removed";

export type AddCampaignMembersPreview = {
  candidates: InfluencerListItem[];
  initialSelectedOnly?: boolean;
  initialSelectedIds?: string[];
  submitOutcome?: CampaignMemberPreviewSubmitOutcome;
  onSubmit?: (input: CampaignMemberAddItem[]) => void;
};

export type CampaignMembersPreview = {
  state: CampaignMemberPreviewState;
  items: CampaignMember[];
  nextCursor: string | null;
  addDrawer?: AddCampaignMembersPreview;
  initialDrawerOpen?: boolean;
  removeOutcome?: CampaignMemberPreviewRemoveOutcome;
};

export type CampaignDetailPreview = {
  campaign: Campaign;
  activeTab?: "basic" | "members";
  members: CampaignMembersPreview;
};
