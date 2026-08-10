"""Build the illustrated per-role operations guide as a Word document.

    pip install python-docx
    python scripts/build_role_guide_docx.py --shots ./my-screenshots --out ./Operations-Guide.docx

The generated document embeds screenshots of a LIVE deployment - console
pages that show the AWS account id, bucket names and URLs. That is exactly
why the document itself is not committed to this public repository, while
this generator is: anyone standing up their own deployment captures their
own screenshots (the expected filenames are listed in SHOT_FILES below -
any that are missing are skipped with a placeholder) and regenerates the
guide for internal distribution.

The text content mirrors docs/guides/ - if the guides change, change this
in the same PR.
"""
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import argparse
import os

_ap = argparse.ArgumentParser()
_ap.add_argument("--shots", default="./ie-screenshots",
                 help="folder of console screenshots (see SHOT_FILES in source)")
_ap.add_argument("--out", default="./Intelligence-Engine-Operations-Guide.docx")
_args = _ap.parse_args()
SHOTS = _args.shots
OUT = _args.out

# The screenshots the document expects. Capture equivalents on your own
# deployment; missing files render as a note instead of failing.
SHOT_FILES = [
    "01-cloudformation-stacks.jpg",   # CloudFormation stack list, filtered to the project
    "02-lakehouse-tiers.jpg",         # lakehouse bucket root: the medallion tier folders
    "03-glue-table-schema.jpg",       # workforce_composition table overview
    "05-stewardship-gate-events.jpg", # stewardship/gate-events/ in S3
    "07-glue-lineage-properties.jpg", # Advanced properties showing the ie.* lineage keys
    "09-stepfunctions-machine.jpg",   # state machine page with execution history
    "10-stepfunctions-definition.jpg",# definition tab: JSON + graph
    "12-execution-graph.png",         # a succeeded execution's graph view
    "13-cloudtrail-trail.jpg",        # trail details: validation, multi-region, data events
    "14-iam-policies.jpg",            # IAM policies page
    "15-consultant-console.jpg",      # consultant home
    "16-engineer-dashboard.jpg",      # /engineer dashboard
    "17-guardrails.jpg",              # /engineer/guardrails
]

doc = Document()

# styles
style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(11)

TEAL = RGBColor(0x0D, 0x94, 0x88)
GREY = RGBColor(0x64, 0x74, 0x8B)


def title(text, size=28, color=TEAL, center=True):
    p = doc.add_paragraph()
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = True
    r.font.color.rgb = color
    return p


def h1(text):
    h = doc.add_heading(text, level=1)
    for r in h.runs:
        r.font.color.rgb = TEAL
    return h


def h2(text):
    return doc.add_heading(text, level=2)


def para(text, bold=False, italic=False, color=None):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.bold = bold
    r.font.italic = italic
    if color:
        r.font.color.rgb = color
    return p


def bullets(items):
    for it in items:
        doc.add_paragraph(it, style="List Bullet")


def steps(items):
    for it in items:
        doc.add_paragraph(it, style="List Number")


def shot(fname, caption, width=6.5):
    path = os.path.join(SHOTS, fname)
    if not os.path.exists(path):
        para(f"[missing screenshot: {fname}]", italic=True, color=GREY)
        return
    doc.add_picture(path, width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    c = doc.add_paragraph()
    c.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = c.add_run(caption)
    r.font.size = Pt(9)
    r.font.italic = True
    r.font.color.rgb = GREY


def code(text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.name = "Consolas"
    r.font.size = Pt(9.5)


# ---------------- cover ----------------
for _ in range(6):
    doc.add_paragraph()
title("Intelligence Engine")
title("Role-by-Role Operations Guide", size=18, color=GREY)
doc.add_paragraph()
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("Every screenshot in this guide is from the live deployed build\n"
              "(AWS us-east-1, August 2026). Nothing is illustrative.")
r.font.size = Pt(11)
r.font.color.rgb = GREY
doc.add_page_break()

# ---------------- orientation ----------------
h1("Orientation: what this platform is")
para("The Intelligence Engine is a pre-engagement intelligence substrate for "
     "management consultants: synthetic (V0) workforce data flows through a "
     "governed medallion data plane, an episodic LLM pipeline turns it into "
     "client briefings, and a hosted console puts a human checkpoint in front "
     "of anything that ships. Everything below is deployed from CloudFormation "
     "templates in the public GitHub repository - 15 stacks, zero drift.")
shot("01-cloudformation-stacks.jpg",
     "The whole system as CloudFormation sees it: 15 stacks, all green. This list is the inventory of record.")
para("Six human roles operate it. Each has a seat with enumerated IAM "
     "permissions, a one-page guide in the repo (docs/guides/), and a "
     "deliberate answer to the question \u201ccan this person block the "
     "system?\u201d Only the consultant can block a run (their own "
     "checkpoints); only the FDE and platform engineer can block "
     "engineering; everyone else is accountable without being load-bearing.")
para("Architecture diagrams for every activity in this guide exist as "
     "Mermaid source in docs/diagrams.md - paste into mermaid.live to "
     "export slide-ready SVG/PNG.", italic=True)
doc.add_page_break()

# ---------------- consultant ----------------
h1("1. Consultant / Engagement Lead")
para("Surface: the hosted console in a browser. No AWS account, no cloud "
     "permissions - a Cognito login created by the data steward.", bold=True)

h2("Activity: run a report")
steps([
    "Sign in at the console URL (ask your team; no self-signup exists).",
    "Pick a client from the list, or enter any company under Research Any Company - entity verification rejects nonsense before any expensive generation starts.",
    "Watch progress live, or close the browser entirely - the run is durable and consumes nothing while paused.",
])
shot("15-consultant-console.jpg",
     "The consultant home: 20 synthetic clients with workforce data, plus free-form company research.")

h2("Activity: decide at checkpoints")
para("The workflow pauses at a midpoint and a final checkpoint. You approve, "
     "request a revision (your feedback is injected into the regeneration "
     "prompts - be concrete), or reject. Revisions are bounded (default two "
     "per checkpoint), so a run cannot loop forever. This is the only place "
     "in the system where a human is deliberately load-bearing.")
shot("12-execution-graph.png",
     "A real run's execution graph: stages (green) flowing into MidpointApproval and FinalApproval - "
     "the pause states cost nothing while a human thinks.")

h2("What you never worry about")
bullets([
    "Your login grants no cloud permissions; the app acts for you under its own scoped identity.",
    "You cannot break the pipeline, the data tiers, or anyone else's run from the console.",
    "Numbers in briefings come from the governed aggregate tier with small cells suppressed - oddly coarse breakdowns are the privacy floor working.",
])
doc.add_page_break()

# ---------------- FDE ----------------
h1("2. Forward-Deployed Engineer (FDE)")
para("Surface: the workbench - an EC2 box inside the account with Claude "
     "Code, git, AWS/Databricks CLIs and Terraform preinstalled. Reached via "
     "SSM only (no SSH exists). The instance role is the credential: no keys "
     "anywhere, every call logged.", bold=True)

h2("Activity: sit down at the terminal")
para("Two equally good paths:")
bullets([
    "Own terminal: AWS CLI + Session Manager plugin (one-time install), then aws ssm start-session --target <workbench-id>.",
    "AWS CloudShell (zero install): the console's built-in terminal ships the CLI and the SSM plugin - the same command works from any browser.",
])
code("aws ec2 start-instances --instance-ids <workbench-id>   # wake the box\n"
     "aws ssm start-session --target <workbench-id>            # connect\n"
     "sudo su - ec2-user && cd /work/intelligence_architecture && claude")

h2("Activity: iterate on the pipeline")
steps([
    "Run stages directly: python stages/enrich_profiles_llm.py sterling-pharma - the seat holds tier-read, artifact-write and Bedrock grants, so the raw loop just works.",
    "Containerize when it matters: ./infrastructure/remote_build.sh --stage-image prints a tag and digest.",
    "Test the full harness against YOUR digest while everyone else runs the blessed one: python scripts/start_harness_run.py --client sterling-pharma --stage-image <uri>@sha256:...",
    "Hold the regression floor: pytest, the in-account golden replay, and the seeded-defect eval (does the reviewer still catch planted arithmetic/unsupported/contradiction errors?).",
])
shot("10-stepfunctions-definition.jpg",
     "The harness definition: execution input pins stage_image by digest, approval states carry "
     "{decision, feedback}, revision loops are bounded.")

h2("Activity: observe a live run")
shot("16-engineer-dashboard.jpg",
     "The engineer dashboard at /engineer: run states, checkpoints, datasets, infrastructure links - "
     "observation deck, not cockpit.")
shot("17-guardrails.jpg",
     "The guardrail surface: input validation, entity verification, output validation, cost controls, "
     "data-access isolation. Read-only when deployed - enforcement changes ride through git.")

h2("What you cannot do, and what to do instead")
bullets([
    "Deploy or modify stacks: edit the template, commit, PR - the platform engineer applies it.",
    "Promote your own image to blessed: hand the tested digest to the deployer seat.",
    "Read the stewardship log: your stewardship-write is append-only; ask the steward.",
])
doc.add_page_break()

# ---------------- platform engineer ----------------
h1("3. Platform Engineer")
para("Surface: the deployer seat (sts:AssumeRole). Operates CloudFormation "
     "on intelligence-engine-* stacks through cfn-exec - a role no human can "
     "assume - and can mutate nothing directly. Boundaries change through "
     "exactly one channel: a reviewed template, deployed as a logged stack "
     "operation.", bold=True)

h2("Activity: routine deploy")
code("./infrastructure/deploy.sh --profile intelligence-deployer \\\n"
     "  --cfn-role arn:aws:iam::<account>:role/intelligence-engine-dev-cfn-exec")
para("Then verify - both scripts are read-only and exit non-zero on failure:")
code("python scripts/qa_sweep.py --profile intelligence-deployer\n"
     "python scripts/iac_coverage.py --profile intelligence-deployer")

h2("Activity: release a pipeline version")
code("python scripts/promote_stage_image.py --profile intelligence-deployer --digest sha256:...")
para("The script verifies the digest exists in ECR, records it as the "
     "blessed image in SSM, and prints the previous digest - rollback is "
     "promoting that previous value back.")

h2("Activity: prove the estate is template-managed")
shot("14-iam-policies.jpg",
     "IAM as the enforcement layer. Filter for intelligence-engine: read and write are always separate "
     "policies, held by different roles.")
para("iac_coverage.py compares every live project resource against "
     "CloudFormation's records; an unmanaged resource is an incident, not "
     "housekeeping.")
doc.add_page_break()

# ---------------- steward ----------------
h1("4. Data Steward")
para("Surface: the steward seat (sts:AssumeRole, 4-hour sessions). Exactly "
     "three capabilities: admit data, admit people, read the audit surface. "
     "Deliberately not critical to any run - a steward absence stops new "
     "admissions, never a report.", bold=True)

h2("Activity: admit a source")
code("aws s3 cp ./drop.jsonl s3://<landing-bucket>/landing/<client>/<source>/<date>/ \\\n"
     "  --profile intelligence-steward")
para("The steward seat is the ONLY identity that can write landing/ - every "
     "object there is something a steward deliberately placed. Downstream, "
     "the anonymization gate checks every governed write automatically.")
shot("02-lakehouse-tiers.jpg",
     "The governed data plane: the medallion tiers as S3 prefixes - foundational (pseudonymous), "
     "derived (aggregates), contextualized (vectors+graph), stewardship (audit log).")

h2("Activity: admit and remove people")
code("aws cognito-idp admin-create-user --profile intelligence-steward \\\n"
     "  --user-pool-id <pool> --username them@example.com ...\n"
     "aws cognito-idp admin-set-user-password --profile intelligence-steward \\\n"
     "  --user-pool-id <pool> --username them@example.com --password '<initial>' --permanent")

h2("Activity: read the audit surface")
shot("05-stewardship-gate-events.jpg",
     "The append-only stewardship log in S3: gate-events/ records every governance decision as JSONL. "
     "Writers cannot read it; only the steward seat can.")
shot("13-cloudtrail-trail.jpg",
     "CloudTrail: multi-region, log-file validation, object-level data events on the governed prefixes - "
     "every individual read of governed data is recorded.")

h2("Activity: review lineage claims")
shot("07-glue-lineage-properties.jpg",
     "Governance as table metadata: ie.owner, ie.lineage.source_table, ie.lineage.rows_in/out (500 -> 37, "
     "21 suppressed), ie.contains_personal_data=false alongside ie.derived_from_personal_data=true.")
doc.add_page_break()

# ---------------- analyst ----------------
h1("5. Data Scientist / Analyst")
para("Surface: Databricks, reading the lakehouse in place through Unity "
     "Catalog - zero-copy, read-only, no AWS credentials in your hands. "
     "Nothing you do in a notebook can corrupt a governed tier.", bold=True)
h2("Working the tiers")
bullets([
    "SQL over derived/ for BI - it is a normal external table with suppression already applied.",
    "foundational/ for record-grain modeling - treat as personal data (pseudonymous, not anonymous).",
    "contextualized/ for retrieval experiments - vectors ship beside their metadata columns.",
    "Never reconstruct suppressed cells by joining/differencing derived outputs - that defeats the tier's control.",
])
shot("03-glue-table-schema.jpg",
     "workforce_composition in the Glue catalog: the schema an analyst queries, with its S3 location in derived/.")
h2("Getting what doesn't exist")
bullets([
    "New/changed product table: a reviewed change to the build stages, via the FDE.",
    "New source entirely: starts with the steward (admission), not engineering.",
])
doc.add_page_break()

# ---------------- admin ----------------
h1("6. Account Admin")
para("Dormant by design. Two jobs: seating people (one sts:AssumeRole grant "
     "per person per seat) and recovery if the deploy channel itself breaks. "
     "Used on a normal day = something is misdesigned.", bold=True)
h2("The one recurring glance (monthly is plenty)")
code("python scripts/qa_sweep.py       # everything healthy, nothing idle-billing\n"
     "python scripts/iac_coverage.py   # nothing exists outside templates")
shot("09-stepfunctions-machine.jpg",
     "The report-build state machine with its execution history - the kind of page an admin glances at "
     "and then closes, because the system runs itself.")

# ---------------- appendix ----------------
doc.add_page_break()
h1("Appendix: where everything lives")
bullets([
    "Repository: github.com/dorchester/intelligence_architecture (public; contains no account-specific values)",
    "Role guides with full commands: docs/guides/",
    "Access model (every identity, every grant): docs/access-model.md",
    "Architecture diagrams as Mermaid source: docs/diagrams.md",
    "The full AWS walkthrough (deploy from nothing): docs/aws-walkthrough.md",
    "This document embeds screenshots of the live account and is for internal distribution - it is intentionally NOT in the public repository.",
])

doc.save(OUT)
print("saved:", OUT)
