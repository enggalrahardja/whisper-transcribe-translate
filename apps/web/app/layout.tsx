import type { Metadata } from "next";
import { AppShell } from "./components/app-shell";
import { RuntimePreferences } from "./components/runtime-preferences";
import "./globals.css";

export const metadata: Metadata = {
  title: "Whisper Transcribe & Translate",
  description: "Online audio transcription, translation, and subtitle workspace.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: `(function(){try{var t=localStorage.getItem('whisper.theme');if(t!=='light'&&t!=='dark'){t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t}catch(e){}})()` }} />
      </head>
      <body>
        <RuntimePreferences />
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
