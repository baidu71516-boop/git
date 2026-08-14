import type { ReactNode } from "react";

import type {
  CurrentMetricsSummary,
  FreshnessStatus,
  JsonValue,
} from "./types";

const integerFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const compactFollowerFormatter = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

const largeCompactFollowerFormatter = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
});

const shanghaiDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const shanghaiMonthDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
});

const shanghaiTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const shanghaiTooltipDateTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const platformLabels: Record<string, string> = {
  xiaohongshu: "小红书",
};

const contactTypeLabels: Record<string, string> = {
  email: "邮箱",
  phone: "手机",
  wechat: "微信",
  other: "其他",
};

const crmStagePresentation: Record<string, { label: string; color?: string }> =
  {
    待开发: { label: "待开发" },
    已发送邮件: { label: "已发送邮件", color: "blue" },
    第一次跟进: { label: "第一次跟进", color: "blue" },
    第二次跟进: { label: "第二次跟进", color: "blue" },
    已回复: { label: "已回复", color: "cyan" },
    已加微信: { label: "已加微信", color: "cyan" },
    沟通中: { label: "沟通中", color: "purple" },
    潜在合作: { label: "潜在合作", color: "gold" },
    高意向: { label: "高意向", color: "orange" },
    暂不考虑: { label: "暂不考虑" },
    长期维护: { label: "长期维护", color: "green" },
    已结束: { label: "已结束" },
  };

const freshnessPresentation: Record<
  FreshnessStatus,
  { label: string; tone: FreshnessStatus }
> = {
  fresh: { label: "新鲜", tone: "fresh" },
  aging: { label: "较旧", tone: "aging" },
  stale: { label: "陈旧", tone: "stale" },
  very_stale: { label: "严重陈旧", tone: "very_stale" },
  unknown: { label: "未知", tone: "unknown" },
};

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

export function formatFollowers(value: number | null): string {
  if (value === null) return "—";
  if (value < 10_000) return integerFormatter.format(value);
  const scaled = value / 10_000;
  const formatter =
    scaled >= 100 ? largeCompactFollowerFormatter : compactFollowerFormatter;
  return `${formatter.format(scaled)}万`;
}

export function formatExactFollowers(value: number): string {
  return integerFormatter.format(value);
}

export function platformLabel(value: string): string {
  return platformLabels[value] ?? value;
}

export function contactTypeLabel(value: string): string {
  return contactTypeLabels[value] ?? "其他";
}

export function crmStageDisplay(value: string | null | undefined): {
  label: string;
  color?: string;
} {
  if (!value) return { label: "未设置" };
  return crmStagePresentation[value] ?? { label: value };
}

export function freshnessDisplay(value: FreshnessStatus | null): {
  label: string;
  tone: FreshnessStatus;
} {
  if (value === null) return { label: "未知", tone: "unknown" };
  return freshnessPresentation[value];
}

export function freshnessExplanation(
  value: FreshnessStatus | null,
  requiresRefresh: boolean,
): string | null {
  if (value !== "unknown") return null;
  return requiresRefresh
    ? "暂无可靠采集记录，建议加入数据更新名单。"
    : "暂无可参与当前灰豚更新流程的数据。";
}

export function freshnessSupportText(
  value: FreshnessStatus | null,
  requiresRefresh: boolean,
): string | null {
  if (value !== "unknown") return null;
  return requiresRefresh ? "暂无可靠采集记录" : "暂无可更新数据";
}

export function latestMetricsTimestamp(
  metrics: CurrentMetricsSummary[],
): string | null {
  let latest: { raw: string; time: number } | null = null;
  for (const metric of metrics) {
    if (!metric.source_updated_at) continue;
    const time = new Date(metric.source_updated_at).getTime();
    if (Number.isNaN(time)) continue;
    if (latest === null || time > latest.time) {
      latest = { raw: metric.source_updated_at, time };
    }
  }
  return latest?.raw ?? null;
}

function calendarDayNumber(date: Date): number {
  const [year, month, day] = shanghaiDay.format(date).split("-").map(Number);
  return Date.UTC(year, month - 1, day) / 86_400_000;
}

export function formatMetricsTimestamp(
  value: string | null,
  now: Date = new Date(),
): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const daysAgo = calendarDayNumber(now) - calendarDayNumber(date);
  if (daysAgo === 0) return `今天 ${shanghaiTime.format(date)}`;
  if (daysAgo === 1) return `昨天 ${shanghaiTime.format(date)}`;
  if (daysAgo > 1 && daysAgo < 7) return `${daysAgo}天前`;
  return shanghaiMonthDay.format(date);
}

export function formatMetricsTimestampTooltip(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : shanghaiTooltipDateTime.format(date).replaceAll("/", "-");
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
