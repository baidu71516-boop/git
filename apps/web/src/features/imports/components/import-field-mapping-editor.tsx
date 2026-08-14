"use client";

import { Select, Typography } from "antd";

const { Text } = Typography;

export const IMPORT_CANONICAL_FIELDS = [
  "nickname",
  "profile_url",
  "platform_account_id",
  "external_source_id",
  "account_handle",
  "email",
  "source_updated_at",
  "bio",
  "gender",
  "region_raw",
  "verification_info",
  "mcn_name",
  "creator_tags",
  "creator_level",
  "is_brand_partner",
  "followers_count",
  "huitun_score",
  "notes_count",
  "likes_collects_total",
  "commercial_notes_count",
  "notes_60d",
  "viral_rate_60d",
  "avg_likes_60d",
  "avg_collects_60d",
  "avg_comments_60d",
  "avg_shares_60d",
  "active_fans_raw",
  "suspicious_fans_raw",
  "fan_gender_raw",
  "fan_region_raw",
  "fan_age_raw",
  "fan_active_time_raw",
  "fan_interests_raw",
  "image_note_price",
  "image_cpe",
  "image_cpm",
  "video_note_price",
  "video_cpe",
  "video_cpm",
] as const;

export type ImportFieldMappingEditorProps = {
  sourceFields: readonly string[];
  canonicalFields: readonly string[];
  value: Readonly<Record<string, string>>;
  onChange: (value: Record<string, string>) => void;
  disabled?: boolean;
};

/**
 * Pure field-mapping presentation shared by Legacy and Bulk owners.
 *
 * Job identity, API mutations, polling, notices, errors and busy state remain
 * outside this component so each workflow keeps its own state machine.
 */
export function ImportFieldMappingEditor({
  sourceFields,
  canonicalFields,
  value,
  onChange,
  disabled = false,
}: ImportFieldMappingEditorProps) {
  return (
    <div className="mapping-grid">
      {sourceFields.map((sourceField) => (
        <div className="mapping-row" key={sourceField}>
          <Text>{sourceField}</Text>
          <Select
            aria-label={`将 ${sourceField} 映射到`}
            allowClear
            showSearch
            value={value[sourceField]}
            disabled={disabled}
            placeholder="不导入"
            onChange={(canonicalField?: string) => {
              const next = { ...value };
              if (canonicalField) next[sourceField] = canonicalField;
              else delete next[sourceField];
              onChange(next);
            }}
            options={canonicalFields.map((field) => ({
              value: field,
              label: field,
            }))}
          />
        </div>
      ))}
    </div>
  );
}
