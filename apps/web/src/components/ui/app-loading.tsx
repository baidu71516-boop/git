import { Spin } from "antd";

export function AppLoading({ label = "正在加载" }: { label?: string }) {
  return (
    <div className="app-loading" role="status" aria-live="polite">
      <Spin size="small" />
      <span>{label}</span>
    </div>
  );
}
