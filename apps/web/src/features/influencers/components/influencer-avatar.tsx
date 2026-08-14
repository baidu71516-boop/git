import { Avatar } from "antd";

import type { CSSProperties } from "react";

type AvatarSize = 40 | 48 | 56;

const BACKGROUND_PALETTE = ["#dbeafe", "#e0e7ff", "#d2e5ea"];

function pickAvatarSizePx(size: AvatarSize): number {
  return size;
}

function pickAvatarLabel(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) {
    return "达";
  }
  const firstHan = [...trimmed].find((char) => /[\u4e00-\u9fff]/.test(char));
  return firstHan ?? trimmed[0];
}

function avatarBackground(name: string): string {
  const total = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return BACKGROUND_PALETTE[total % BACKGROUND_PALETTE.length];
}

const sizeFontMap: Record<AvatarSize, number> = {
  40: 18,
  48: 20,
  56: 23,
};

export function InfluencerAvatar({
  name,
  avatarUrl,
  size = 40,
  square = false,
}: {
  name: string;
  avatarUrl?: string | null;
  size?: AvatarSize;
  square?: boolean;
}) {
  const sizePx = pickAvatarSizePx(size);
  const hasAvatar = Boolean(avatarUrl?.trim());
  const fallback = pickAvatarLabel(name);

  const style: CSSProperties | undefined = hasAvatar
    ? undefined
    : {
        backgroundColor: avatarBackground(name || fallback),
        color: "#102a56",
        fontSize: sizeFontMap[size],
      };

  return (
    <Avatar
      size={sizePx}
      src={avatarUrl || undefined}
      className={`influencer-avatar ${square ? "influencer-avatar-square" : "influencer-avatar-circle"}`}
      shape={square ? "square" : "circle"}
      style={style}
      draggable={false}
    >
      {fallback}
    </Avatar>
  );
}
