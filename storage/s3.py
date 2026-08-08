"""S3 storage backend with structural run isolation."""

from __future__ import annotations

import boto3


from storage.base import StorageBackend


class S3Storage(StorageBackend):
    """S3 storage with client/run prefix isolation."""

    def __init__(self, bucket: str, region: str = "us-east-1", profile: str | None = None):
        self.bucket = bucket
        session_kwargs = {}
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs, region_name=region)
        self.s3 = session.client("s3")

    def write(self, run_id: str, client_id: str, category: str, filename: str, data: bytes) -> str:
        key = self._build_key(client_id, run_id, category, filename)
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        return f"s3://{self.bucket}/{key}"

    def read(self, run_id: str, client_id: str, category: str, filename: str) -> bytes:
        key = self._build_key(client_id, run_id, category, filename)
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def exists(self, run_id: str, client_id: str, category: str, filename: str) -> bool:
        key = self._build_key(client_id, run_id, category, filename)
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.s3.exceptions.ClientError:
            return False

    def list_files(self, run_id: str, client_id: str, category: str) -> list[str]:
        prefix = self._build_key(client_id, run_id, category, "")
        response = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        if "Contents" not in response:
            return []
        return [obj["Key"].split("/")[-1] for obj in response["Contents"] if obj["Key"] != prefix]
