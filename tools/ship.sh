#!/bin/bash
# ship.sh PR SHA VERSION : wait for checks on SHA, merge PR, wait until release VERSION is published.
R=repos/willwalsh14-hub/voice-companion-updates
PR=$1; SHA=$2; VER=$3
for i in $(seq 1 170); do
  out=$(gh api "$R/commits/$SHA/check-runs" --jq '.check_runs[] | "\(.name)|\(.status)|\(.conclusion)"')
  n=$(echo "$out" | grep -c '|completed|'); t=$(echo "$out" | grep -c .); f=$(echo "$out" | grep -c '|failure')
  if [ "$f" -gt 0 ]; then echo "CHECKS FAILED $VER"; echo "$out"
    for id in $(gh api "$R/commits/$SHA/check-runs" --jq '.check_runs[]|select(.conclusion=="failure")|.id'); do gh api $R/check-runs/$id/annotations --jq '.[]|"\(.title): \(.message)"' | head -c 7000; echo; done; exit 1; fi
  if [ "$t" -ge 3 ] && [ "$n" -eq "$t" ]; then break; fi; sleep 45; done
head=$(gh api $R/pulls/$PR --jq .head.sha); [ "$head" = "$SHA" ] || { echo "PR head moved: $head"; exit 1; }
m=$(gh api -X PUT $R/pulls/$PR/merge -f merge_method=merge -f sha=$SHA -f commit_title="Release $VER (#$PR)" --jq .sha) || { echo "MERGE FAILED"; exit 1; }
echo "merged $VER as $m"; sleep 90
for i in $(seq 1 170); do
  out=$(gh api "$R/commits/$m/check-runs" --jq '.check_runs[] | "\(.name)|\(.status)|\(.conclusion)"')
  rel=$(gh api $R/releases/latest --jq .tag_name); f=$(echo "$out" | grep -c '|failure')
  if [ "$f" -gt 0 ]; then echo "PUBLISH BUILD FAILED $VER"; echo "$out"
    for id in $(gh api "$R/commits/$m/check-runs" --jq '.check_runs[]|select(.conclusion=="failure")|.id'); do gh api $R/check-runs/$id/annotations --jq '.[]|"\(.title): \(.message)"' | head -c 7000; echo; done
    echo "RUN_ID $(gh api "$R/commits/$m/check-runs" --jq '.check_runs[]|select(.name=="installer")|.details_url')"; exit 2; fi
  n=$(echo "$out" | grep -c '|completed|'); t=$(echo "$out" | grep -c .)
  if [ "$rel" = "v$VER" ] && [ "$n" -eq "$t" ]; then echo "PUBLISHED $VER"; exit 0; fi; sleep 45; done
echo TIMEOUT; exit 3
