"""Methodology-driven agent engine.

The agent reads a Markdown methodology playbook and reasons through
its steps, invoking tools and generating narrative as guided by the playbook.
This demonstrates the architectural pattern where methodology drives execution.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from agent.context import RunContext, Stage
from agent.model import BedrockModel

logger = logging.getLogger("intelligence_engine.agent")

METHODOLOGY_DIR = Path(__file__).resolve().parent.parent / "methodology"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


TOOL_DEFINITIONS = [
    {
        "name": "run_workforce_analysis",
        "description": "Analyze a workforce CSV file and return metrics including headcount, department breakdown, tenure statistics, and turnover risk percentages. The CSV must be in the run's input directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the CSV file in the run's input directory",
                }
            },
            "required": ["filename"],
        },
    },
    {
        "name": "generate_chart",
        "description": "Generate a headcount-by-department bar chart from analysis metrics. Returns the chart filename.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics_json": {
                    "type": "string",
                    "description": "JSON string of the metrics dictionary from workforce analysis",
                }
            },
            "required": ["metrics_json"],
        },
    },
    {
        "name": "generate_report",
        "description": "Render the final HTML report from metrics, narrative text, and chart. Returns the output path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics_json": {
                    "type": "string",
                    "description": "JSON string of the metrics dictionary",
                },
                "narrative": {
                    "type": "string",
                    "description": "The narrative analysis text to include in the report",
                },
            },
            "required": ["metrics_json", "narrative"],
        },
    },
]


class AgentEngine:
    """Methodology-driven agent that reasons through a playbook using tool-use."""

    def __init__(self, ctx: RunContext, model: BedrockModel):
        self.ctx = ctx
        self.model = model
        self._metrics: dict | None = None
        self._chart_data: bytes | None = None

    def execute(self, methodology_file: str = "thin_slice.md") -> str:
        """Execute the agent workflow following the specified methodology."""
        methodology = (METHODOLOGY_DIR / methodology_file).read_text()
        system_prompt = (PROMPTS_DIR / "system.md").read_text()

        system = (
            f"{system_prompt}\n\n"
            f"## Current Run Context\n"
            f"- Run ID: {self.ctx.run_id}\n"
            f"- Client: {self.ctx.client_name} (ID: {self.ctx.client_id})\n"
            f"- Methodology Version: {self.ctx.methodology_version}\n"
            f"- Available input files: {self.ctx.list_artifacts('input')}\n\n"
            f"## Methodology\n\n{methodology}\n\n"
            f"## Instructions\n"
            f"Follow the methodology steps in order. Use the provided tools to "
            f"perform analysis, generate charts, and produce the final report. "
            f"For each step, explain your reasoning briefly before invoking tools."
        )

        messages = [
            {
                "role": "user",
                "content": (
                    "Execute the workflow as described in the methodology. "
                    "Begin with step 1 and proceed through all steps. "
                    "The input file is already loaded in the run's input directory."
                ),
            }
        ]

        logger.info(
            "Agent starting methodology execution",
            extra={"run_id": self.ctx.run_id, "methodology": methodology_file},
        )

        output_path = None
        max_turns = 10

        for turn in range(max_turns):
            start = time.time()
            response = self._invoke_with_tools(system, messages)
            elapsed = time.time() - start

            logger.info(
                "Agent turn %d completed in %.1fs",
                turn + 1,
                elapsed,
                extra={
                    "run_id": self.ctx.run_id,
                    "turn": turn + 1,
                    "stop_reason": response.get("stop_reason"),
                },
            )

            # Accumulate assistant response
            messages.append({"role": "assistant", "content": response["content"]})

            if response["stop_reason"] == "end_turn":
                break

            if response["stop_reason"] == "tool_use":
                tool_results = self._handle_tool_calls(response["content"])
                messages.append({"role": "user", "content": tool_results})

                # Check if report was generated
                for result in tool_results:
                    if (
                        result.get("type") == "tool_result"
                        and "report.html" in result.get("content", "")
                    ):
                        output_path = result["content"]

        if not output_path and self.ctx.artifact_exists("output", "report.html"):
            output_path = f"output/report.html"

        logger.info(
            "Agent execution complete",
            extra={"run_id": self.ctx.run_id, "output": output_path},
        )

        return output_path or ""

    def _invoke_with_tools(self, system: str, messages: list) -> dict:
        """Invoke the model with tool definitions."""
        body = {
            "anthropic_version": self.model.config.anthropic_version,
            "max_tokens": 4096,
            "temperature": 0.3,
            "system": system,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
        }

        import json as json_mod
        response = self.model.client.invoke_model(
            modelId=self.model.config.model_id,
            contentType="application/json",
            accept="application/json",
            body=json_mod.dumps(body),
        )
        return json_mod.loads(response["body"].read())

    def _handle_tool_calls(self, content: list) -> list:
        """Process tool calls and return results."""
        results = []
        for block in content:
            if block.get("type") != "tool_use":
                continue

            tool_name = block["name"]
            tool_input = block["input"]
            tool_id = block["id"]

            logger.info(
                "Tool call: %s",
                tool_name,
                extra={"run_id": self.ctx.run_id, "tool": tool_name, "input": tool_input},
            )

            try:
                result = self._execute_tool(tool_name, tool_input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result,
                })
            except Exception as e:
                logger.error(
                    "Tool error: %s - %s",
                    tool_name,
                    str(e),
                    extra={"run_id": self.ctx.run_id},
                )
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": f"Error: {str(e)}",
                    "is_error": True,
                })

        return results

    def _execute_tool(self, name: str, inputs: dict) -> str:
        """Execute a named tool and return its result as a string."""
        if name == "run_workforce_analysis":
            return self._tool_workforce_analysis(inputs["filename"])
        elif name == "generate_chart":
            return self._tool_generate_chart(inputs["metrics_json"])
        elif name == "generate_report":
            return self._tool_generate_report(inputs["metrics_json"], inputs["narrative"])
        else:
            raise ValueError(f"Unknown tool: {name}")

    def _tool_workforce_analysis(self, filename: str) -> str:
        """Run workforce analysis on an input file."""
        import tempfile
        from tools.basic_analysis import run_workforce_analysis

        data = self.ctx.read_artifact("input", filename)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(data)
            f.flush()
            metrics = run_workforce_analysis(Path(f.name))

        self._metrics = metrics
        self.ctx.advance_to(Stage.ANALYSIS_COMPLETE)

        metrics_json = json.dumps(metrics, indent=2)
        self.ctx.write_artifact("working", "metrics.json", metrics_json.encode())
        return metrics_json

    def _tool_generate_chart(self, metrics_json: str) -> str:
        """Generate a chart from metrics."""
        import tempfile
        from tools.chart import generate_headcount_chart

        metrics = json.loads(metrics_json)
        with tempfile.TemporaryDirectory() as tmp:
            chart_path = generate_headcount_chart(metrics, Path(tmp))
            self._chart_data = chart_path.read_bytes()

        self.ctx.write_artifact("working", "headcount_by_department.png", self._chart_data)
        return "Chart generated: headcount_by_department.png"

    def _tool_generate_report(self, metrics_json: str, narrative: str) -> str:
        """Generate the final HTML report."""
        from tools.report import render_report

        metrics = json.loads(metrics_json)
        self.ctx.model_id = self.model.model_id

        if self._chart_data is None:
            raise RuntimeError("Chart must be generated before report")

        html = render_report(self.ctx, metrics, narrative, self._chart_data)
        output_path = self.ctx.write_artifact("output", "report.html", html.encode("utf-8"))
        self.ctx.write_artifact("working", "narrative.txt", narrative.encode("utf-8"))
        self.ctx.advance_to(Stage.REPORT_GENERATED)
        return output_path
