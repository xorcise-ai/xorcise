import type { Metadata } from "next";
import { MissionCatalog } from "@/features/missions/catalog";

export const metadata: Metadata = { title: "Mission Catalog" };

export default function MissionsPage() {
  return <MissionCatalog />;
}
