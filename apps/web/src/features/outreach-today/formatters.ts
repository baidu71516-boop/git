import type {
  TodayChannel,
  TodayContactFilter,
  TodayItem,
  TodayPriority,
} from "./types";

const shanghaiDateTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const shanghaiTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const channelLabels: Record<TodayChannel, string> = {
  EMAIL: "邮箱",
  XIAOHONGSHU_PRIVATE_MESSAGE: "小红书私信",
  DOUYIN_PRIVATE_MESSAGE: "抖音私信",
  WECHAT: "微信",
  MANUAL: "人工",
};

const contactFilterLabels: Record<TodayContactFilter, string> = {
  has_contact: "有联系方式",
  has_email: "有邮箱",
  no_contact: "无联系方式",
};

const priorityLabels: Record<TodayPriority, string> = {
  HIGH: "高",
  NORMAL: "普通",
};

function dateKey(value: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(value));
  const get = (type: string) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function channelLabel(value: TodayChannel): string {
  return channelLabels[value];
}

export function contactFilterLabel(value: TodayContactFilter): string {
  return contactFilterLabels[value];
}

export function priorityLabel(value: TodayPriority): string {
  return priorityLabels[value];
}

export function formatBusinessDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return value;
  return `${match[1]}年${Number(match[2])}月${Number(match[3])}日`;
}

export function formatAsOf(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : shanghaiTime.format(date);
}

export function formatShanghaiDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : shanghaiDateTime.format(date);
}

export function formatTodayDueAt(
  value: string,
  businessDate: string,
  asOf: string,
): { label: string; overdue: boolean; title: string } {
  const date = new Date(value);
  const reference = new Date(asOf);
  if (Number.isNaN(date.getTime()))
    return { label: "—", overdue: false, title: "—" };
  const overdue =
    !Number.isNaN(reference.getTime()) && date.getTime() < reference.getTime();
  const sameDay = dateKey(value) === businessDate;
  const label =
    sameDay && !overdue
      ? `今天 ${shanghaiTime.format(date)}`
      : overdue
        ? `已逾期 · ${shanghaiDateTime.format(date)}`
        : shanghaiDateTime.format(date);
  return { label, overdue, title: shanghaiDateTime.format(date) };
}

export function taskKindLabel(value: TodayItem["kind"]): string {
  return value === "FIRST_TOUCH" ? "首次触达" : "跟进";
}

export function contactSummary(
  item: Pick<TodayItem, "has_contact" | "has_email">,
): string {
  if (item.has_email) return "有邮箱";
  if (item.has_contact) return "有联系方式";
  return "无联系方式";
}

export function preferredAccountSecondary(item: TodayItem): string {
  const account = item.preferred_platform_account;
  const platform =
    account.platform === "xiaohongshu" ? "小红书" : account.platform;
  return account.account_handle
    ? `${platform} · ${account.account_handle}`
    : platform;
}
