"""Bedrock model abstraction.

Provides a configurable interface to Claude models via Amazon Bedrock,
keeping the runtime model selection independent of analytical code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import boto3


@dataclass
class ModelConfig:
    model_id: str = "us.anthropic.claude-sonnet-4-6"
    region: str = "us-east-1"
    profile: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.3
    anthropic_version: str = "bedrock-2023-05-31"


class BedrockModel:
    """Thin wrapper around Bedrock InvokeModel for Claude."""

    def __init__(self, config: ModelConfig | None = None):
        self.config = config or ModelConfig()
        session_kwargs = {}
        if self.config.profile:
            session_kwargs["profile_name"] = self.config.profile
        session = boto3.Session(**session_kwargs, region_name=self.config.region)
        self.client = session.client("bedrock-runtime")

    def invoke(
        self,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Invoke the model and return the text response."""
        body = {
            "anthropic_version": self.config.anthropic_version,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system

        response = self.client.invoke_model(
            modelId=self.config.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )

        result = json.loads(response["body"].read())
        return result["content"][0]["text"]

    @property
    def model_id(self) -> str:
        return self.config.model_id
