import type { ReactNode } from "react";

export default function InfluencersLayout({
  children,
  drawer,
}: Readonly<{
  children: ReactNode;
  drawer: ReactNode;
}>) {
  return (
    <>
      {children}
      {drawer}
    </>
  );
}
