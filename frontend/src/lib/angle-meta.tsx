import { Flame, Sparkles, TrendingUp } from "lucide-react";
import type { AngleType } from "@/lib/types";

export const ANGLE_META: Record<
  AngleType,
  { label: string; icon: React.ReactNode; tone: string }
> = {
  pain_led: {
    label: "Pain-led",
    icon: <Flame className="h-3.5 w-3.5" />,
    tone: "text-[hsl(var(--angle-pain))]",
  },
  trigger_led: {
    label: "Trigger-led",
    icon: <TrendingUp className="h-3.5 w-3.5" />,
    tone: "text-[hsl(var(--angle-trigger))]",
  },
  outcome_led: {
    label: "Outcome-led",
    icon: <Sparkles className="h-3.5 w-3.5" />,
    tone: "text-[hsl(var(--angle-outcome))]",
  },
};

export function getAngleMeta(type: string) {
  return ANGLE_META[type as AngleType] ?? ANGLE_META.pain_led;
}
