"""Move commits off the workbench without giving the workbench a git credential.

The workbench authenticates to AWS by instance role and holds no GitHub
identity, so `git push` from it cannot work and should not be made to work:
a shared analysis box with write access to the public repository is a wider
blast radius than the seat needs.

This script is the supported path instead. On the workbench:

    python scripts/handoff_patch.py export --count 1

which writes a patch to `s3://<runs-bucket>/handoff/` using the instance role.
Then, from a machine that already has git credentials:

    python scripts/handoff_patch.py fetch --apply

which downloads the newest patch and applies it with `git am`, preserving the
original author, message and timestamps. Review, then push as yourself.

The patch travels through a bucket the workbench can already write and the
engineer can already read, so no new credential is introduced at either end.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PREFIX = "handoff"


def _run(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def _runs_bucket() -> str:
    """The runs bucket, resolved at run time - its name embeds the account ID."""
    import boto3

    session = boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1"))
    account = session.client("sts").get_caller_identity()["Account"]
    environment = os.environ.get("IE_ENV", "dev")
    return f"intelligence-engine-{environment}-runs-{account}"


def _s3():
    import boto3

    return boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1")).client("s3")


def export(count: int) -> int:
    bucket = _runs_bucket()
    sha = _run(["git", "rev-parse", "--short", "HEAD"]).strip()
    patch = _run(["git", "format-patch", f"-{count}", "HEAD", "--stdout"])
    if not patch.strip():
        print("SKIP | nothing to export")
        return 0

    key = f"{PREFIX}/{sha}.patch"
    _s3().put_object(Bucket=bucket, Key=key, Body=patch.encode())
    print(f"OK | exported {count} commit(s) at {sha} -> s3://{bucket}/{key}")
    print("     collect with: python scripts/handoff_patch.py fetch --apply")
    return 0


def fetch(apply_patch: bool) -> int:
    bucket = _runs_bucket()
    s3 = _s3()
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=f"{PREFIX}/").get("Contents", [])
    if not listing:
        print(f"SKIP | no patches waiting in s3://{bucket}/{PREFIX}/")
        return 0

    newest = max(listing, key=lambda o: o["LastModified"])
    body = s3.get_object(Bucket=bucket, Key=newest["Key"])["Body"].read()
    print(f"OK | newest patch {newest['Key']} ({len(body)} bytes, {newest['LastModified']:%Y-%m-%d %H:%M} UTC)")

    if not apply_patch:
        print("     re-run with --apply to apply it, or inspect it first")
        sys.stdout.write(body.decode(errors="replace")[:2000])
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "handoff.patch"
        path.write_bytes(body)
        try:
            _run(["git", "am", "--keep-cr", str(path)])
        except subprocess.CalledProcessError as e:
            print(f"ERROR | git am failed: {e.stderr.strip()[:400]}")
            print("        resolve, then `git am --continue` or `git am --abort`")
            return 1

    print(f"OK | applied as {_run(['git', 'log', '--oneline', '-1']).strip()}")
    print("     review it, then push as yourself")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    exporter = sub.add_parser("export", help="write HEAD commits to the handoff prefix")
    exporter.add_argument("--count", type=int, default=1, help="how many commits from HEAD")

    fetcher = sub.add_parser("fetch", help="collect the newest patch")
    fetcher.add_argument("--apply", action="store_true", help="apply it with git am")

    args = parser.parse_args()
    if args.command == "export":
        return export(args.count)
    return fetch(args.apply)


if __name__ == "__main__":
    sys.exit(main())
