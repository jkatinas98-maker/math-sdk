"""
Builds the exact file set Stake Engine's studio portal (Files tab) expects
for a math-version upload, from the already-generated
games/scratch/library/{books,lookup}_<mode>.{jsonl,csv} files:

  publish/
    index.json
    books_<mode>.jsonl.zst   (payoutMultiplier scaled x100 integer, per
                               docs/rgs_docs/data_format.md: 1150 == 11.50x)
    lookup_<mode>.csv        (no header; id,weight,payoutMultiplier(x100 int))

Run AFTER scratch_sim.py (needs its library/ output already on disk).
Requires the `pzstd`/`zstd` CLI (falls back to plain .jsonl if neither is
on PATH -- the portal will reject that, so this prints a clear warning).
"""
import csv
import json
import shutil
import subprocess
from pathlib import Path

LIB_DIR = Path(__file__).parent / "library"
OUT_DIR = LIB_DIR / "publish"
MODES = ["easy", "medium", "hard"]
COST = 1.0

ZSTD_BIN = shutil.which("zstd") or shutil.which("pzstd") or \
    "/nix/store/gwy8kliqcqspz7r56y6hn2a0k28r5hak-zstd-1.5.7/bin/pzstd"


def scaled(mult: float) -> int:
    """Fixed-point x100 integer, matching the docs' 11.5x -> 1150 example."""
    return round(mult * 100)


def build_mode(mode: str):
    books_in = LIB_DIR / f"books_{mode}.jsonl"
    lookup_in = LIB_DIR / f"lookup_{mode}.csv"
    books_out = OUT_DIR / f"books_{mode}.jsonl"
    lookup_out = OUT_DIR / f"lookup_{mode}.csv"

    # Books: rescale payoutMultiplier to the required integer fixed-point form.
    with open(books_in) as fin, open(books_out, "w") as fout:
        for line in fin:
            row = json.loads(line)
            row["payoutMultiplier"] = scaled(row["payoutMultiplier"])
            fout.write(json.dumps(row) + "\n")

    # Lookup: no header, columns id,weight,payoutMultiplier(x100 int) --
    # matches docs/rgs_docs/data_format.md's CSV example exactly.
    with open(lookup_in) as fin, open(lookup_out, "w", newline="") as fout:
        reader = csv.DictReader(fin)
        writer = csv.writer(fout)
        for row in reader:
            writer.writerow([row["id"], row["weight"], scaled(float(row["payoutMultiplier"]))])

    # Compress the books file -- required (.jsonl.zst), what the RGS actually reads.
    zst_path = books_out.with_suffix(".jsonl.zst")
    if ZSTD_BIN and Path(ZSTD_BIN).exists() or shutil.which(str(ZSTD_BIN)):
        subprocess.run([str(ZSTD_BIN), "-f", "-19", str(books_out), "-o", str(zst_path)],
                        check=True, capture_output=True)
        books_out.unlink()  # portal wants the compressed file only
        return zst_path.name, lookup_out.name
    else:
        print(f"  WARNING: no zstd binary found -- left {books_out.name} uncompressed. "
              f"The portal requires .jsonl.zst; compress it yourself before uploading.")
        return books_out.name, lookup_out.name


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    modes_manifest = []
    for mode in MODES:
        events_file, weights_file = build_mode(mode)
        modes_manifest.append({
            "name": mode,
            "cost": COST,
            "events": events_file,
            "weights": weights_file,
        })
        print(f"{mode}: wrote {events_file} + {weights_file}")

    index = {"modes": modes_manifest}
    with open(OUT_DIR / "index.json", "w") as f:
        json.dump(index, f, indent=2)

    print(f"\nPublish package ready in: {OUT_DIR}")
    print("Upload every file in that folder (index.json + the 3 .jsonl.zst + 3 .csv) "
          "via the Files tab in studio.engine.io for the EMOJI SCRATCH game.")


if __name__ == "__main__":
    main()
