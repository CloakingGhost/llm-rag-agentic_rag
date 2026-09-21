"use client";

import { KeyRound, Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "소개" },
  { href: "/chat", label: "챗봇" },
  { href: "/dashboard", label: "대시보드" },
];

export function SiteHeader({ onOpenKeyPanel }: { onOpenKeyPanel?: () => void }) {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-2 px-4">
        <Link href="/" className="font-heading text-sm font-semibold sm:text-base">
          소비자 분쟁 Q&amp;A
        </Link>

        <nav className="ml-4 hidden items-center gap-1 sm:flex">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground",
                pathname === item.href && "bg-accent text-foreground",
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-1">
          {onOpenKeyPanel ? (
            <Button variant="ghost" size="icon" aria-label="API 키 설정" onClick={onOpenKeyPanel}>
              <KeyRound className="size-4" />
            </Button>
          ) : null}
          <ThemeToggle />
          <DropdownMenu>
            <DropdownMenuTrigger asChild className="sm:hidden">
              <Button variant="ghost" size="icon" aria-label="메뉴">
                <Menu className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {NAV.map((item) => (
                <DropdownMenuItem key={item.href} asChild>
                  <Link href={item.href}>{item.label}</Link>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
}
