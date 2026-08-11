import type { ReactNode } from "react";

import type { JsonValue } from "./types";

const shanghaiDateTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function displayValue(value: string | number | boolean | null): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

export function formatShanghaiDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : shanghaiDateTime.format(date);
}

export function renderJsonValue(value: JsonValue, path = "metric"): ReactNode {
  if (value === null) return "—";
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return displayValue(value);
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    return (
      <ul className="metric-value-list">
        {value.map((item, index) => (
          <li key={`${path}-${index}`}>
            {renderJsonValue(item, `${path}-${index}`)}
          </li>
        ))}
      </ul>
    );
  }
  const entries = Object.entries(value);
  if (entries.length === 0) return "—";
  return (
    <dl className="metric-value-object">
      {entries.map(([key, item]) => (
        <div key={`${path}-${key}`}>
          <dt>{key}</dt>
          <dd>{renderJsonValue(item, `${path}-${key}`)}</dd>
        </div>
      ))}
    </dl>
  );
}

const metricLabels: Record<string, string> = {
  followers_count: "粉丝数",
  notes_count: "笔记数",
  likes_collects_total: "获赞与收藏总数",
  commercial_notes_count: "商业笔记数",
  notes_60d: "近 60 天笔记数",
  viral_rate_60d: "近 60 天爆文率",
  avg_likes_60d: "近 60 天平均点赞",
  avg_collects_60d: "近 60 天平均收藏",
  avg_comments_60d: "近 60 天平均评论",
  avg_shares_60d: "近 60 天平均分享",
  huitun_score: "灰豚指数",
  active_fans_raw: "活跃粉丝原始值",
  active_fans_rate: "活跃粉丝比例",
  active_fans_count: "活跃粉丝数",
  suspicious_fans_raw: "疑似粉丝原始值",
  suspicious_fans_rate: "疑似粉丝比例",
  suspicious_fans_count: "疑似粉丝数",
  fan_gender_raw: "粉丝性别原始值",
  fan_male_rate: "男性粉丝比例",
  fan_female_rate: "女性粉丝比例",
  fan_region_raw: "粉丝地区原始值",
  fan_region_distribution: "粉丝地区分布",
  fan_age_raw: "粉丝年龄原始值",
  fan_age_distribution: "粉丝年龄分布",
  fan_active_time_raw: "粉丝活跃时间原始值",
  fan_active_time_distribution: "粉丝活跃时间分布",
  fan_interests_raw: "粉丝兴趣原始值",
  fan_interests_distribution: "粉丝兴趣分布",
  image_note_price: "图文笔记价格",
  image_cpe: "图文 CPE",
  image_cpm: "图文 CPM",
  video_note_price: "视频笔记价格",
  video_cpe: "视频 CPE",
  video_cpm: "视频 CPM",
};

export function metricLabel(key: string): string {
  return metricLabels[key] ?? key;
}
