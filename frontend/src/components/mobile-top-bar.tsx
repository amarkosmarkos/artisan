"use client";

import { MarkosLogo } from "@/components/markos-logo";
import { ThemeToggle } from "@/components/theme-provider";

export function MobileTopBar() {
  return (
    <header className="flex md:hidden items-center justify-between border-b border-border/50 bg-card/30 px-4 py-3 backdrop-blur-xl sticky top-0 z-20">
      <MarkosLogo size="sm" />
      <ThemeToggle compact />
    </header>
  );
}
