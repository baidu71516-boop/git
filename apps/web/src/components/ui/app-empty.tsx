import { Empty } from "antd";

export function AppEmpty({ description }: { description: string }) {
  return (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
  );
}
