import type { Metadata } from "next";
import { Sidebar } from "./components/sidebar";
import { RuntimePreferences } from "./components/runtime-preferences";
import "./globals.css";

export const metadata: Metadata = {
  title: "Whisper Transcribe & Translate",
  description: "Online audio transcription, translation, and subtitle workspace.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <RuntimePreferences />
        <div className="shell">
          <Sidebar />
          <main className="content">{children}</main>
        </div>
      </body>
    </html>
  );
}
