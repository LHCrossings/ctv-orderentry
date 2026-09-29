"""Remove S3 files of program assets Etere has marked expired — Etere's "Delete on -> AWS S3"
workflow in batches. Program assets only; commercials, PSAs, PIs, IDs, bumpers, opens and
anything marked HIATUS are never touched (see services/s3_media_purge.py).

    uv run python3 scripts/purge_s3_media.py                    # dry run: plan CSV in logs/s3-purge/
    uv run python3 scripts/purge_s3_media.py --apply --limit 100
    uv run python3 scripts/purge_s3_media.py --apply

Credentials: AWS keys from credentials.env / .env (the same the web assets page uses);
Etere DB from .env via etere_direct_client.connect().
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "browser_automation"))

from browser_automation.etere_direct_client import connect  # noqa: E402
from src.business_logic.services import s3_media_purge as smp  # noqa: E402


def _cfg() -> dict[str, str]:
    cfg: dict[str, str] = {}
    for name in ("credentials.env", ".env"):
        p = ROOT / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg.setdefault(k.strip(), v.strip())
    return cfg


def _s3(cfg: dict[str, str]):
    import boto3

    if cfg.get("AWS_ACCESS_KEY_ID") and cfg.get("AWS_SECRET_ACCESS_KEY"):
        return boto3.client(
            "s3",
            region_name=cfg.get("AWS_REGION", "us-west-2"),
            aws_access_key_id=cfg["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=cfg["AWS_SECRET_ACCESS_KEY"],
        )
    return boto3.Session(profile_name="crossings").client("s3", region_name="us-west-2")


def _gb(b: int) -> str:
    return f"{b / 1e9:,.1f} GB"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sweep", choices=["expired"], default="expired")
    ap.add_argument(
        "--apply", action="store_true", help="delete; without it only the plan is written"
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="stop after N files (first batch trial)"
    )
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    a = ap.parse_args()

    cfg = _cfg()
    bucket = cfg.get("S3_BUCKET_NAME", "storageforct")
    s3 = _s3(cfg)
    conn = connect()

    rows = smp.fetch_expired(conn, a.today)
    listing = smp.probe_sizes(s3, bucket, (r.get("file_name") for r in rows))
    ok, bad = smp.check_rows(rows, listing)
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    plan = ROOT / "logs" / "s3-purge" / f"s3-purge-plan-{a.sweep}-{stamp}.csv"
    smp.write_plan_csv(plan, ok, bad)

    print(f"{a.sweep} sweep as of {a.today}: {len(rows)} S3 metafile(s) on expired program assets")
    print(f"  deletable: {len(ok)} file(s), {_gb(sum(int(r['size']) for r in ok))}")
    print(f"  skipped:   {len(bad)}")
    for r in bad[:20]:
        print(f"     {r.get('file_name') or r.get('file_id')}: {r['problem']}")
    if len(bad) > 20:
        print(f"     … {len(bad) - 20} more in the plan CSV")
    print("  top families:")
    for fam, n, b in smp.summarize(ok):
        print(f"     {fam:16s} {n:6d}  {_gb(b):>12s}")
    print(f"  plan: {plan}")
    if not ok:
        return
    if not a.apply:
        print(
            "DRY RUN — add --apply to delete (S3 objects cannot be restored; FS rows can, from the restore .sql)"
        )
        return

    res = smp.purge(
        conn, s3, bucket, ok, apply=True, limit=a.limit, restore_dir=ROOT / "logs" / "s3-purge"
    )
    conn.close()
    print(
        f"deleted {res['deleted_s3']} object(s) / {res['deleted_db']} record(s), {_gb(res['bytes'])}"
    )
    for e in res["s3_errors"]:
        print(f"  S3 error: {e}")
    done = ok[: a.limit] if a.limit else ok
    v = smp.verify(connect(), s3, bucket, done)
    print(
        f"verify (fresh connection): FS rows left {v['fs_rows_left']}, objects left {len(v['objects_left'])}"
    )
    if not v["ok"]:
        for k in v["objects_left"][:10]:
            print(f"  still in S3: {k}")
        sys.exit("VERIFY FAILED — see above; rerun the dry run to see what is left")
    print("OK")


if __name__ == "__main__":
    main()
