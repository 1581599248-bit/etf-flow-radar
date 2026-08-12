import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ETF资金雷达｜A股ETF资金行为研究终端",
  description: "聚合指数级ETF份额变化，研究资金流向、异常强度、轮动与历史位置。",
  openGraph: { title: "ETF资金雷达", description: "A股ETF资金行为研究终端", images: ["/og.png"] },
  twitter: { card: "summary_large_image", title: "ETF资金雷达", description: "A股ETF资金行为研究终端", images: ["/og.png"] },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
