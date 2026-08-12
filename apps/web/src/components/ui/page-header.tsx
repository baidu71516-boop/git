import { Breadcrumb, Typography } from "antd";
import type { BreadcrumbProps } from "antd";
import type { ReactNode } from "react";

const { Text, Title } = Typography;

export function PageHeader({
  title,
  description,
  breadcrumb,
  extra,
}: {
  title: string;
  description?: string;
  breadcrumb?: BreadcrumbProps["items"];
  extra?: ReactNode;
}) {
  return (
    <header className="app-page-header">
      <div>
        {breadcrumb?.length ? <Breadcrumb items={breadcrumb} /> : null}
        <Title level={2}>{title}</Title>
        {description ? <Text type="secondary">{description}</Text> : null}
      </div>
      {extra ? <div className="app-page-header-extra">{extra}</div> : null}
    </header>
  );
}
