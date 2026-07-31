import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const shellSource = readFileSync(new URL("../app/components/app-shell.tsx", import.meta.url), "utf8");
const sidebarSource = readFileSync(new URL("../app/components/sidebar.tsx", import.meta.url), "utf8");
const runtimeSource = readFileSync(new URL("../app/components/runtime-preferences.tsx", import.meta.url), "utf8");
const layoutSource = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

test("application shell provides persistent desktop collapse and a mobile drawer", () => {
  assert.match(layoutSource, /<AppShell>\{children\}<\/AppShell>/);
  assert.match(shellSource, /whisper\.sidebar\.collapsed/);
  assert.match(shellSource, /localStorage\.setItem\(SIDEBAR_STORAGE_KEY/);
  assert.match(shellSource, /sidebar-mobile-open/);
  assert.match(shellSource, /Close navigation menu/);
  assert.match(cssSource, /\.shell\.sidebar-collapsed/);
  assert.match(cssSource, /@media \(max-width: 760px\)/);
  assert.match(cssSource, /\.sidebar-mobile-open \.sidebar/);
});

test("collapsed navigation remains identifiable through icons and titles", () => {
  assert.match(sidebarSource, /NavigationIcon/);
  assert.match(sidebarSource, /title=\{collapsed \? item\.label/);
  assert.match(sidebarSource, /aria-current=\{isActive \? "page"/);
  assert.match(sidebarSource, /sidebar-theme-button/);
});

test("theme initializes before paint and persists without fighting application settings", () => {
  assert.match(layoutSource, /prefers-color-scheme: dark/);
  assert.match(layoutSource, /document\.documentElement\.dataset\.theme=t/);
  assert.match(runtimeSource, /THEME_STORAGE_KEY = "whisper\.theme"/);
  assert.match(runtimeSource, /storedTheme === "light" \|\| storedTheme === "dark"/);
  assert.match(runtimeSource, /preference === "system"\) window\.localStorage\.removeItem/);
  assert.match(shellSource, /whisper-theme-change/);
});

test("dark mode defines semantic surfaces and fixes live status card contrast", () => {
  assert.match(cssSource, /--app-bg: #0b1020/);
  assert.match(cssSource, /--surface-raised: #1a2135/);
  assert.match(cssSource, /:root\[data-theme="dark"\] \.live-status-grid > div/);
  assert.match(cssSource, /background: var\(--surface-muted\)/);
  assert.match(cssSource, /color: var\(--text-soft\)/);
});
