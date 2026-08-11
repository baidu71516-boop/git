"use client";

import { Button, Space, Typography } from "antd";

const { Text } = Typography;

type InfluencerPaginationProps = {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
};

export function InfluencerPagination({
  page,
  pageSize,
  total,
  onPageChange,
}: InfluencerPaginationProps) {
  return (
    <div className="influencer-pagination">
      <Text>共 {total} 位达人</Text>
      <Space>
        <Button
          aria-label="上一页"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </Button>
        <Text>第 {page} 页</Text>
        <Button
          aria-label="下一页"
          disabled={page * pageSize >= total}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </Button>
      </Space>
    </div>
  );
}
