# Base image for workflow build stages.
#
# Python for analytical stages, Node + Playwright + Chromium because the
# render check is a blocking QA gate - a stage must be able to load the
# generated HTML in a real browser and fail the build if it does not render.
#
# Built in-account by CodeBuild (never on a laptop) and pushed to the
# stages ECR repository. See infrastructure/cloudformation/workflow.yaml.

# bookworm pinned deliberately: playwright's --with-deps resolves its apt
# package list against Debian 12; trixie renamed the font packages and the
# browser install fails there.
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    MPLBACKEND=Agg

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        "boto3>=1.34" \
        "pandas>=2.0" \
        "pyyaml>=6.0" \
        "jinja2>=3.1"

# The analytical stack. An orchestration substrate that cannot run the
# statistics is not a substrate for this workload: raking and Beta-Binomial
# work need scipy, the fits need scikit-learn, the charts need matplotlib,
# and the EVM workbooks need openpyxl. MPLBACKEND=Agg above keeps matplotlib
# headless - without it, importing pyplot in a container tries to find a
# display and fails.
#
# pyarrow is what makes the silver layer readable: the lakehouse is parquet,
# and pandas cannot open it otherwise.
RUN pip install --no-cache-dir \
        "numpy>=1.26" \
        "scipy>=1.11" \
        "scikit-learn>=1.4" \
        "matplotlib>=3.8" \
        "openpyxl>=3.1" \
        "pyarrow>=15.0"

# Playwright with its own dependency install - this is what makes the render
# gate real rather than aspirational.
RUN npm install -g playwright@1.49.1 \
    && playwright install --with-deps chromium \
    && npm cache clean --force

# Stages run as a non-root user with a writable workspace.
RUN useradd --create-home --uid 10002 stage \
    && mkdir -p /workspace \
    && chown -R stage:stage /workspace /opt/playwright
USER stage
WORKDIR /workspace

CMD ["python", "--version"]
