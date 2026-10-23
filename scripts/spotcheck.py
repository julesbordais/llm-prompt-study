"""Hand-score the 15 sampled outputs in the terminal (fills data/human_spotcheck.csv).
Re-run to resume: already-scored rows are skipped."""
import csv
import textwrap
from pathlib import Path

P = Path("data/human_spotcheck.csv")
rows = list(csv.DictReader(P.open(encoding="utf-8")))
fields = list(rows[0].keys())


def ask(label):
    while True:
        v = input(f"  {label} (1-5, q=save & quit): ").strip().lower()
        if v == "q" or v in {"1", "2", "3", "4", "5"}:
            return v
        print("  -> type a number from 1 to 5")


def save():
    with P.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


print("FAITHFULNESS  5=all facts, nothing invented | 4=one minor fact missing | 3=several missing | 2=wrong/invented fact | 1=mostly wrong")
print("ACTIONABILITY 5=specific, correct, right urgency | 4=correct but generic | 3=vague | 2=wrong step/urgency | 1=harmful/empty")
for i, r in enumerate(rows, 1):
    if r["faithfulness"] and r["actionability"]:
        continue
    print("\n" + "=" * 80 + f"\n[{i}/{len(rows)}] {r['case_id']}")
    print("TICKET:\n" + textwrap.indent(textwrap.fill(r["ticket"], 100), "  "))
    print("SUMMARY:\n" + textwrap.indent(textwrap.fill(r["summary"], 100), "  "))
    print("NEXT ACTION:\n" + textwrap.indent(textwrap.fill(r["next_action"], 100), "  "))
    f = ask("faithfulness")
    if f == "q":
        break
    a = ask("actionability")
    if a == "q":
        break
    r["faithfulness"], r["actionability"] = f, a
    save()
save()
done = sum(1 for r in rows if r["faithfulness"] and r["actionability"])
print(f"\nScored {done}/{len(rows)}. Saved to {P}")
