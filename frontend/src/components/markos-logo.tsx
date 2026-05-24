import Image from "next/image";
import Link from "next/link";
import { cn } from "@/lib/utils";
import logo from "@/markos_artisan_logo.png";

interface MarkosLogoProps {
  className?: string;
  showWordmark?: boolean;
  /** Omit or pass null to render without a link. */
  href?: string | null;
  size?: "sm" | "md";
}

export function MarkosLogo({
  className,
  showWordmark = true,
  href = "/",
  size = "md",
}: MarkosLogoProps) {
  const imgSize = size === "sm" ? 28 : 32;

  const content = (
    <>
      <Image
        src={logo}
        alt="Markos Artisan"
        width={imgSize}
        height={imgSize}
        className="shrink-0 rounded-md ring-1 ring-border/50 shadow-sm dark:ring-border/70"
        priority
      />
      {showWordmark && (
        <span className="text-sm font-semibold tracking-tight">
          Markos Artisan
        </span>
      )}
    </>
  );

  if (href != null) {
    return (
      <Link
        href={href}
        className={cn("flex items-center gap-2.5", className)}
      >
        {content}
      </Link>
    );
  }

  return (
    <div className={cn("flex items-center gap-2.5", className)}>{content}</div>
  );
}
