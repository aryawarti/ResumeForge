"""S3-compatible object storage for generated PDFs.

MinIO locally, Cloudflare R2 in production -- the same boto3 code drives both,
which is the point of keeping the difference in configuration. R2 is the
free-tier choice because it has no egress fees and a genuinely free storage
allowance, and because Render's free instances have no persistent disk to
write to in the first place.

PDFs are served through short-lived presigned URLs rather than proxied through
the API. On a 0.1 CPU instance, streaming file bytes through the web process
is exactly the wrong use of the only CPU you have.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = ["StorageError", "Storage", "get_storage"]


class StorageError(RuntimeError):
    pass


class Storage:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.s3_endpoint_url or None,
                aws_access_key_id=self.settings.s3_access_key,
                aws_secret_access_key=self.settings.s3_secret_key,
                region_name=self.settings.s3_region,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                    # botocore defaults both of these to 60s. Unbounded, a
                    # storage host that is merely unreachable -- MinIO not
                    # started, R2 having a bad day -- turns into a three-minute
                    # hang on whatever called us, which during startup meant
                    # the API never began serving at all.
                    connect_timeout=5,
                    read_timeout=15,
                ),
            )
        return self._client

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist. Safe to call repeatedly."""
        bucket = self.settings.s3_bucket
        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in {"404", "NoSuchBucket", "403"}:
                raise StorageError(f"could not reach bucket {bucket}: {exc}") from exc
            try:
                self.client.create_bucket(Bucket=bucket)
                logger.info("created bucket %s", bucket)
            except ClientError as create_exc:
                raise StorageError(
                    f"could not create bucket {bucket}: {create_exc}"
                ) from create_exc

    def put_pdf(self, key: str, data: bytes) -> str:
        try:
            self.client.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=data,
                ContentType="application/pdf",
            )
        except ClientError as exc:
            raise StorageError(f"upload failed for {key}: {exc}") from exc
        return key

    def presigned_url(self, key: str, filename: str = "resume.pdf") -> str:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.settings.s3_bucket,
                    "Key": key,
                    "ResponseContentDisposition": f'inline; filename="{filename}"',
                },
                ExpiresIn=self.settings.presigned_url_ttl_seconds,
            )
        except ClientError as exc:
            raise StorageError(f"could not sign url for {key}: {exc}") from exc

    def get_pdf(self, key: str) -> bytes:
        try:
            response = self.client.get_object(
                Bucket=self.settings.s3_bucket, Key=key
            )
            return response["Body"].read()
        except ClientError as exc:
            raise StorageError(f"download failed for {key}: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)
        except ClientError as exc:
            logger.warning("delete failed for %s: %s", key, exc)


@lru_cache
def get_storage() -> Storage:
    return Storage()
