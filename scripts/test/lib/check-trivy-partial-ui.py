#!/usr/bin/env python3
"""Keep the Trivy partial-list marker visible after client-side refetches."""

from pathlib import Path
import sys

TABLE = Path("portal/src/components/security/vulnerabilities-table.tsx")
PAGE = Path("portal/src/app/(dashboard)/security/page.tsx")


def valid(table: str, page: str) -> bool:
  return (
    'response.headers.get("X-Partial-Data") === "true"' in table
    and 'result.partial && <DataState state="partial" detailKey="securityPartial" />' in table
    and "Boolean(summary?.partial) || vulnList.partial" in page
    and "<VulnerabilitiesTable initialData={initialVulns} />" in page
  )


def main() -> int:
  table = TABLE.read_text()
  page = PAGE.read_text()
  if not valid(table, page):
    raise AssertionError("Trivy partial state must flow from API headers and server initial data to the table banner")

  if "--mutation-verify" in sys.argv:
    mutated = table.replace('response.headers.get("X-Partial-Data") === "true"', "false", 1)
    if mutated == table or valid(mutated, page):
      raise AssertionError("partial-header mutation was not rejected")
    print("PASS: Trivy partial UI contract; header-removal mutation rejected")
  else:
    print("PASS: Trivy partial UI contract")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
