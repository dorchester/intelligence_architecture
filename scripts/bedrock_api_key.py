"""Mint a short-term Bedrock API key and print what Claude Code needs.

A long-term Bedrock API key is an IAM service-specific credential, so it needs
an IAM user and it keeps working until somebody remembers to delete it. A
short-term key is derived from credentials you already hold - an SSO session,
an assumed seat, an instance role - carries exactly the permissions of that
identity, and expires on its own (12 hours by default).

    aws sso login --profile intelligence-dev
    python scripts/bedrock_api_key.py --profile intelligence-dev

Then paste the two exports it prints, and run `claude`. Nothing is written to
disk, and the key is never stored in this repository.

If your workflow genuinely cannot use a short-lived key, deploy
infrastructure/cloudformation/bedrock-api-key-user.yaml for that person and
mint a long-term one against the user it declares.
"""
from __future__ import annotations

import argparse
import os
import sys

WINDOW_HOURS = 12


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", help="AWS profile to derive the key from")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--export", action="store_true",
                    help="print only the export lines, for eval $(...)")
    args = ap.parse_args()

    try:
        import boto3
    except ImportError:
        print("ERROR | boto3 is required", file=sys.stderr)
        return 1

    try:
        from aws_bedrock_token_generator import provide_token
    except ImportError:
        print("ERROR | the token generator is not installed:\n"
              "        pip install aws-bedrock-token-generator\n"
              "        (or use the Bedrock console: API keys -> short-term)",
              file=sys.stderr)
        return 1

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if session.get_credentials() is None:
        print(f"ERROR | no credentials for profile {args.profile or '(default)'};"
              " try: aws sso login", file=sys.stderr)
        return 1

    # Fail here rather than handing over a key that cannot call anything.
    try:
        identity = session.client("sts").get_caller_identity()
    except Exception as e:  # noqa: BLE001 - surface whatever the SDK says
        print(f"ERROR | could not verify the session: {e}", file=sys.stderr)
        return 1

    token = provide_token(session=session, region=args.region)

    if args.export:
        print(f"export AWS_BEARER_TOKEN_BEDROCK={token}")
        print("export CLAUDE_CODE_USE_BEDROCK=1")
        return 0

    print(f"OK | minted from {identity['Arn']}")
    print(f"     region {args.region}, valid ~{WINDOW_HOURS}h, "
          "carries exactly this identity's Bedrock permissions")
    print()
    print("Paste these, then run `claude`:")
    print()
    print(f"  export AWS_BEARER_TOKEN_BEDROCK={token}")
    print("  export CLAUDE_CODE_USE_BEDROCK=1")
    print()
    print("Treat it like a password: it is a bearer token. Do not commit it,")
    print("paste it into a ticket, or send it over chat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
