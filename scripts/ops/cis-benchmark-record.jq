# D3: redact before truncation; whole suspicious fields are removed, including
# command-line credential flags, PEM/JWT and URL credentials. Cost: some file-path
# evidence is hidden too; escape hatch: review a narrower allow-list offline.
def safe($limit):
  tostring | if test("(?i)(token|password|passwd|secret|credential|private.key|client.key|bearer|-----BEGIN|eyJ[A-Za-z0-9_-]+\\.|://[^ /]+:[^ /]+@)")
  then "[REDACTED]" else .[:$limit] end;
def rows:
  [.Controls[] | .tests[] | .section as $section |
    if ($section | type) != "string" then error("invalid section type") else . end | .results[] |
    {controlId: .test_number, section: ($section | safe(400)), text: (.test_desc | safe(400)),
     status, scored, type: (if .type == "manual" then "manual" else "automated" end),
     actualValue: ((.actual_value // "") | safe(400)),
     expectedResult: ((.expected_result // "") | safe(400)),
     reason: ((.reason // "") | safe(400))}];
. as $raw |
($raw | rows) as $results |
if ($results | length) == 0 or
   any($results[]; (.status != "PASS" and .status != "FAIL" and .status != "WARN" and .status != "INFO") or
       (.controlId | type) != "string" or (.scored | type) != "boolean")
then error("invalid or empty benchmark results") else . end |
{pass: ([$results[] | select(.status == "PASS")] | length),
 fail: ([$results[] | select(.status == "FAIL")] | length),
 warn: ([$results[] | select(.status == "WARN")] | length),
 info: ([$results[] | select(.status == "INFO")] | length)} as $totals |
if $raw.Totals != {total_pass: $totals.pass, total_fail: $totals.fail,
                       total_warn: $totals.warn, total_info: $totals.info}
   or any($raw.Controls[]; .version != $meta.benchmarkId)
then error("benchmark or totals mismatch") else . end |
($meta | del(.reason)) + {status: "complete", totals: $totals, results: $results}
