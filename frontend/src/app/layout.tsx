import "@/styles/globals.css";
import "katex/dist/katex.min.css";

import { type Metadata } from "next";
import localFont from "next/font/local";

import { ThemeProvider } from "@/components/theme-provider";
import { I18nProvider } from "@/core/i18n/context";
import { detectLocaleServer } from "@/core/i18n/server";

const jakarta = localFont({
  src: [
    {
      path: "../assets/fonts/plus-jakarta-sans-normal.woff2",
      weight: "200 800",
      style: "normal",
    },
    {
      path: "../assets/fonts/plus-jakarta-sans-italic.woff2",
      weight: "200 800",
      style: "italic",
    },
  ],
  variable: "--font-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Trainer AI",
  description: "A LangChain-based framework for building super agents.",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const locale = await detectLocaleServer();
  return (
    <html lang={locale} suppressContentEditableWarning suppressHydrationWarning>
      <body className={jakarta.variable}>
        <ThemeProvider attribute="class" enableSystem defaultTheme="light">
          <I18nProvider initialLocale={locale}>{children}</I18nProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
