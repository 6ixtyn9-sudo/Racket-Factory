import pandas as pd
import argparse
import logging
from racketfactory.assay import assay_segment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("audit")

def run_audit(warehouse_path: str):
    df = pd.read_csv(warehouse_path, low_memory=False)
    
    segments = {
        "Overall": df,
        "ATP": df[df['tour'] == 'ATP'],
        "WTA": df[df['tour'] == 'WTA'],
        "Hard": df[df['_surface'] == 'Hard'],
        "Clay": df[df['_surface'] == 'Clay'],
        "Grass": df[df['_surface'] == 'Grass'],
    }
    
    results = []
    for name, data in segments.items():
        res = assay_segment(data)
        results.append({
            "Segment": name,
            "N": res.n,
            "WinRate": f"{res.win_rate:.2%}",
            "ROI": f"{res.roi:.2%}",
            "WilsonLB": f"{res.wilson_lb:.2%}",
            "Grade": res.grade,
            "Verdict": res.verdict
        })
    
    report = pd.DataFrame(results)
    print("\n=== RACKET FACTORY MARKET AUDIT ===\n")
    print(report.to_string(index=False))
    print("\n==================================\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", default="localdata/warehouse.csv.gz")
    args = parser.parse_args()
    run_audit(args.warehouse)
