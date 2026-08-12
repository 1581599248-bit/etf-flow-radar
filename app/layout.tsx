import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "资金ETF流动每日跟踪｜证据优先研究日报",
  description: "基于交易所真实ETF份额与同日单位净值的证据优先资金流研究看板。",
  openGraph: { title: "资金ETF流动每日跟踪", description: "A股ETF真实份额与资金强度研究日报", images: ["/og.png"] },
  twitter: { card: "summary_large_image", title: "资金ETF流动每日跟踪", description: "A股ETF真实份额与资金强度研究日报", images: ["/og.png"] },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
