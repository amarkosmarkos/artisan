import { CompaniesTable } from "@/components/companies-table";

export const dynamic = "force-dynamic";

export default function TargetsPage() {
  return (
    <CompaniesTable
      role="target"
      title="Targets"
      description="Companies you've evaluated for outbound."
      detailHrefBase="/targets"
    />
  );
}
