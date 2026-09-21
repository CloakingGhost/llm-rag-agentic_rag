"use client";

import { SiteHeader } from "@/components/site-header";
import { useApiKey } from "@/lib/api-key";

export function SiteChrome() {
  const { openPanel } = useApiKey();
  return <SiteHeader onOpenKeyPanel={openPanel} />;
}
