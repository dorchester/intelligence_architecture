"""Promote a stage-image digest to "blessed" - the default the harness runs.

    python scripts/promote_stage_image.py --profile intelligence-deployer --tag latest
    python scripts/promote_stage_image.py --profile intelligence-deployer --digest sha256:abc...

Promotion is a release act, so it belongs to the deployer seat (which holds
ssm:PutParameter on the project path and ecr:DescribeImages, and nothing
mutable beyond that). The FDE proposes a digest after testing it through the
harness; whoever holds the deployer seat promotes it.

What "blessed" means mechanically: the digest is recorded at

    /intelligence-engine/<env>/stages/blessed-image

as a full ECR image URI pinned by digest. scripts/start_harness_run.py
resolves it when no explicit --stage-image is given, so runs are always
against either the blessed pipeline or an explicitly named candidate - never
against "whatever :latest happens to be".

The script refuses to bless a digest that does not exist in the repository,
so a typo cannot dangle the default.
"""
from __future__ import annotations

import argparse
import sys

import boto3

REPO = "intelligence-engine-{env}-stages"
PARAM = "/intelligence-engine/{env}/stages/blessed-image"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--env", default="dev")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tag", help="resolve this tag's current digest and bless it")
    g.add_argument("--digest", help="bless this digest directly (sha256:...)")
    args = ap.parse_args()

    kw = {"region_name": args.region}
    if args.profile:
        kw["profile_name"] = args.profile
    s = boto3.Session(**kw)
    repo = REPO.format(env=args.env)
    param = PARAM.format(env=args.env)

    ecr = s.client("ecr")
    image_id = {"imageTag": args.tag} if args.tag else {"imageDigest": args.digest}
    r = ecr.describe_images(repositoryName=repo, imageIds=[image_id])
    img = r["imageDetails"][0]
    digest = img["imageDigest"]
    registry = ecr.describe_repositories(repositoryNames=[repo])["repositories"][0]["repositoryUri"]
    uri = f"{registry}@{digest}"

    prev = None
    ssm = s.client("ssm")
    try:
        prev = ssm.get_parameter(Name=param)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        pass

    ssm.put_parameter(Name=param, Value=uri, Type="String", Overwrite=True)

    print(f"blessed: {uri}")
    print(f"  pushed: {img['imagePushedAt']:%Y-%m-%d %H:%M} | tags: {', '.join(img.get('imageTags', []) or ['<none>'])}")
    if prev and prev != uri:
        print(f"  was:    {prev}")
        print("  rollback is re-running this script with the previous digest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
