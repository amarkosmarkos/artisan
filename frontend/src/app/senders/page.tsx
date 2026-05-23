import { CompaniesTable } from "@/components/companies-table";

export const dynamic = "force-dynamic";

export default function SendersPage() {
  return (
    <CompaniesTable
      role="sender"
      title="Senders"
      description="Companies whose ICP and value proposition you've inferred."
      detailHrefBase="/senders"
    />
  );
}
