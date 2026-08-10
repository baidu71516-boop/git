"use client";

import {
  CheckCircleOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Card, Flex, Space, Tag, Typography } from "antd";

const { Paragraph, Text, Title } = Typography;

export function PhaseZeroStatus() {
  return (
    <main className="status-shell">
      <Card className="status-card" bordered={false}>
        <Space direction="vertical" size="large">
          <Tag color="processing">PHASE 0</Tag>
          <div>
            <Title level={1}>达人智能触达系统</Title>
            <Paragraph className="subtitle">
              工程基础设施已启动。业务功能将在后续阶段按验收顺序实现。
            </Paragraph>
          </div>
          <Flex vertical gap={12}>
            <Text>
              <CheckCircleOutlined className="success-icon" /> Web 服务运行正常
            </Text>
            <Text>
              <SafetyCertificateOutlined className="success-icon" />{" "}
              尚未启用真实 AI、邮件或业务操作
            </Text>
          </Flex>
        </Space>
      </Card>
    </main>
  );
}
