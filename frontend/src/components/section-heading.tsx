import { cn } from "@/lib/utils";

type Level = "page" | "section" | "subsection";

const LEVEL_STYLES: Record<Level, string> = {
  page: "text-xl font-semibold tracking-tight",
  section: "text-lg font-semibold tracking-tight",
  subsection: "text-sm font-semibold tracking-tight",
};

interface SectionHeadingProps {
  title: string;
  description?: string;
  level?: Level;
  className?: string;
}

export function SectionHeading({
  title,
  description,
  level = "section",
  className,
}: SectionHeadingProps) {
  const Tag = level === "subsection" ? "h3" : "h2";

  return (
    <div className={cn("space-y-1", className)}>
      <Tag className={LEVEL_STYLES[level]}>{title}</Tag>
      {description && (
        <p className="text-sm text-muted-foreground leading-relaxed">
          {description}
        </p>
      )}
    </div>
  );
}
