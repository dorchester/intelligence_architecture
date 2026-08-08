"""Authoritative run state management via DynamoDB.

Tracks run lifecycle, stage transitions, checkpoints, and provenance metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


class RunStateManager:
    """Manages run state in DynamoDB with checkpoint support."""

    def __init__(self, table_name: str, region: str = "us-east-1", profile: str | None = None):
        session_kwargs = {}
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs, region_name=region)
        dynamodb = session.resource("dynamodb")
        self.table = dynamodb.Table(table_name)

    def create_run(
        self,
        run_id: str,
        client_id: str,
        client_name: str,
        model_id: str = "",
        methodology_version: str = "0.1.0",
        code_version: str = "0.1.0",
    ) -> dict:
        """Create a new run record."""
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "run_id": run_id,
            "client_id": client_id,
            "client_name": client_name,
            "stage": "initialized",
            "model_id": model_id,
            "methodology_version": methodology_version,
            "code_version": code_version,
            "created_at": now,
            "updated_at": now,
            "checkpoints": [],
        }
        self.table.put_item(Item=item)
        return item

    def update_stage(self, run_id: str, stage: str) -> None:
        """Advance a run to a new stage."""
        now = datetime.now(timezone.utc).isoformat()
        self.table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET stage = :s, updated_at = :t",
            ExpressionAttributeValues={":s": stage, ":t": now},
        )

    def request_approval(self, run_id: str, checkpoint_name: str, description: str) -> None:
        """Mark a run as waiting for human approval at a checkpoint."""
        now = datetime.now(timezone.utc).isoformat()
        checkpoint = {
            "name": checkpoint_name,
            "description": description,
            "status": "pending",
            "requested_at": now,
        }
        self.table.update_item(
            Key={"run_id": run_id},
            UpdateExpression=(
                "SET stage = :s, updated_at = :t, "
                "current_checkpoint = :cp"
            ),
            ExpressionAttributeValues={
                ":s": "waiting_for_approval",
                ":t": now,
                ":cp": checkpoint,
            },
        )

    def approve_checkpoint(self, run_id: str, approved_by: str = "operator") -> None:
        """Approve the current checkpoint and resume the run."""
        now = datetime.now(timezone.utc).isoformat()
        run = self.get_run(run_id)
        checkpoint = run.get("current_checkpoint", {})
        checkpoint["status"] = "approved"
        checkpoint["resolved_at"] = now
        checkpoint["resolved_by"] = approved_by

        self.table.update_item(
            Key={"run_id": run_id},
            UpdateExpression=(
                "SET stage = :s, updated_at = :t, "
                "current_checkpoint = :cp, "
                "checkpoints = list_append(if_not_exists(checkpoints, :empty), :hist)"
            ),
            ExpressionAttributeValues={
                ":s": "running",
                ":t": now,
                ":cp": {},
                ":hist": [checkpoint],
                ":empty": [],
            },
        )

    def reject_checkpoint(self, run_id: str, reason: str, rejected_by: str = "operator") -> None:
        """Reject the current checkpoint."""
        now = datetime.now(timezone.utc).isoformat()
        run = self.get_run(run_id)
        checkpoint = run.get("current_checkpoint", {})
        checkpoint["status"] = "rejected"
        checkpoint["reason"] = reason
        checkpoint["resolved_at"] = now
        checkpoint["resolved_by"] = rejected_by

        self.table.update_item(
            Key={"run_id": run_id},
            UpdateExpression=(
                "SET stage = :s, updated_at = :t, "
                "current_checkpoint = :cp, "
                "checkpoints = list_append(if_not_exists(checkpoints, :empty), :hist)"
            ),
            ExpressionAttributeValues={
                ":s": "rejected",
                ":t": now,
                ":cp": {},
                ":hist": [checkpoint],
                ":empty": [],
            },
        )

    def complete_run(self, run_id: str, output_location: str) -> None:
        """Mark a run as completed."""
        now = datetime.now(timezone.utc).isoformat()
        self.table.update_item(
            Key={"run_id": run_id},
            UpdateExpression="SET stage = :s, updated_at = :t, output_location = :o, completed_at = :c",
            ExpressionAttributeValues={
                ":s": "completed",
                ":t": now,
                ":o": output_location,
                ":c": now,
            },
        )

    def get_run(self, run_id: str) -> dict | None:
        """Get a run by ID."""
        response = self.table.get_item(Key={"run_id": run_id})
        return response.get("Item")

    def list_runs_for_client(self, client_id: str) -> list[dict]:
        """List all runs for a client."""
        response = self.table.query(
            IndexName="client-index",
            KeyConditionExpression=Key("client_id").eq(client_id),
        )
        return response.get("Items", [])
