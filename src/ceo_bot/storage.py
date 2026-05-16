from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from functools import cache

import boto3

from ceo_bot.config import settings


@cache
def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.do_spaces_endpoint,
        region_name=settings.do_spaces_region,
        aws_access_key_id=settings.do_spaces_access_key,
        aws_secret_access_key=settings.do_spaces_secret_key,
    )


def object_key(message_id: int, filename: str) -> str:
    ts = datetime.now(UTC).strftime("%Y/%m/%d")
    return f"attachments/{ts}/{message_id}/{filename}"


def put_object(key: str, data: bytes, content_type: str | None = None) -> tuple[str, str]:
    """Upload bytes to Spaces and return (key, sha256)."""
    digest = hashlib.sha256(data).hexdigest()
    extra: dict[str, str] = {}
    if content_type:
        extra["ContentType"] = content_type
    _client().put_object(
        Bucket=settings.do_spaces_bucket,
        Key=key,
        Body=data,
        **extra,
    )
    return key, digest


def get_object(key: str) -> bytes:
    resp = _client().get_object(Bucket=settings.do_spaces_bucket, Key=key)
    return resp["Body"].read()


def presigned_url(key: str, expires_in: int = 3600) -> str:
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.do_spaces_bucket, "Key": key},
        ExpiresIn=expires_in,
    )
