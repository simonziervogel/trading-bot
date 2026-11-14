"""make_figures.py — regenerate every figure in docs/images from data/.

Reads only the committed data/ snapshot, so it runs from a fresh clone with no
API key. `--tables` also prints the numbers quoted in the README and the
post-mortem, so each of them can be traced to a command.

Usage:
    python make_figures.py                      # all figures -> docs/images/
    python make_figures.py --only calibration   # one figure
    python make_figures.py --tables             # figures + summary tables
"""

import argparse
import inspect
from pathlib import Path

from kalshi.report import data as D
from kalshi.report import style as S
from kalshi.report.figures import FIGURES


def _print_tables(data_dir) -> None:
    fmt = {"win_rate": "{:.1%}".format, "wr_lo": "{:.1%}".format, "wr_hi": "{:.1%}".format,
           "avg_price": "{:.3f}".format, "ev": "{:+.4f}".format, "ev_lo": "{:+.4f}".format,
           "ev_hi": "{:+.4f}".format, "gross_ev": "{:+.4f}".format, "z": "{:+.2f}".format}
    for side, label in (("no", "NO side"), ("yes", "YES side"), (None, "all signals")):
        tab = D.monthly_before_after(side=side, data_dir=data_dir)
        print(f"\n== Fair Value before/after, {label} ==")
        print(tab.to_string(index=False, formatters=fmt))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--data-dir", default="data")
    p.add_argument("--out-dir", default="docs/images")
    p.add_argument("--only", nargs="+", choices=sorted(FIGURES), default=None)
    p.add_argument("--tables", action="store_true", help="also print the summary tables")
    args = p.parse_args()

    data_dir = Path(args.data_dir)
    for name, fn in FIGURES.items():
        if args.only and name not in args.only:
            continue
        takes_data = "data_dir" in inspect.signature(fn).parameters
        fig = fn(data_dir=data_dir) if takes_data else fn()
        S.save(fig, Path(args.out_dir) / f"{name}.png")

    if args.tables:
        _print_tables(data_dir)


if __name__ == "__main__":
    main()
