"""S3-compatible object storage helper (MinIO in dev/prod).

Primary backend is S3/MinIO via boto3. When S3 is not configured (local dev and
the test suite) the same functions transparently read/write a local directory so
callers never branch on the backend.
"""
from __future__ import annotations

import logging
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core import metrics
from app.core.config import settings

logger = logging.getLogger("lifelink.storage")
# Phase 30: storage observability. Every log line and metric below is keyed
# only by operation name + backend ("s3"/"local") + exception class name --
# NEVER by the object key (verifications/certificates keys embed a
# user-supplied original filename, e.g. "verifications/<user_id>/<req_id>/
# <uuid>_passport_jane_doe.pdf" -- see app/api/routes/verifications.py --
# which can itself be PII) and never by document contents or a signed URL.
# `StorageObjectNotFoundError` is deliberately NOT counted as a failure
# here -- "the object genuinely doesn't exist" is an expected, already-
# handled outcome (a 404 to the caller), not a storage-backend problem;
# only a real put/get/delete/bucket-setup error against the backend itself
# counts as a storage failure for monitoring purposes.


class StorageObjectNotFoundError(Exception):
    """Raised by ``get_bytes`` when the key genuinely does not exist in the
    configured backend -- normalized across both backends (a local
    ``FileNotFoundError`` and an S3 ``ClientError`` with a 404/NoSuchKey
    code) so every caller can distinguish "this object was never stored, or
    was deleted" (an honest 404 to the client) from "the storage backend
    itself is unreachable/erroring" (an honest 503) -- see Phase 20's
    hardening of `app/api/routes/verifications.py::admin_get_verification_document`
    and `app/api/routes/certificates.py::download_certificate`, both of
    which previously let *any* storage exception (missing object included)
    escape as an unhandled exception."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"storage object not found: {key}")


def storage_backend() -> str:
    """`"s3"` when MinIO/S3 is configured, else `"local"`."""
    if settings.s3_endpoint_url and settings.s3_access_key and settings.s3_secret_key:
        return "s3"
    return "local"


def _local_root() -> Path:
    root = Path(settings.storage_local_dir or (Path.cwd() / ".local-storage"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_path(key: str) -> Path:
    # Keys are app-generated (no user input); still guard against traversal.
    safe = key.replace("\\", "/").lstrip("/")
    if ".." in safe.split("/"):
        raise ValueError("invalid storage key")
    return _local_root() / safe


@lru_cache
def get_s3_client() -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


def ensure_bucket() -> None:
    if storage_backend() != "s3":
        _local_root()
        return
    try:
        client = get_s3_client()
        existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
        if settings.s3_bucket not in existing:
            client.create_bucket(Bucket=settings.s3_bucket)
    except Exception as exc:
        metrics.inc_storage_failure("ensure_bucket")
        logger.warning("storage ensure_bucket failed (backend=s3): %s", type(exc).__name__)
        raise


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    backend = storage_backend()
    try:
        if backend == "s3":
            get_s3_client().put_object(
                Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
            )
            return
        path = _local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except Exception as exc:
        metrics.inc_storage_failure("put")
        logger.warning("storage put_bytes failed (backend=%s): %s", backend, type(exc).__name__)
        raise


def get_bytes(key: str) -> bytes:
    backend = storage_backend()
    if backend == "s3":
        from botocore.exceptions import ClientError

        try:
            obj = get_s3_client().get_object(Bucket=settings.s3_bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                raise StorageObjectNotFoundError(key) from exc
            metrics.inc_storage_failure("get")
            logger.warning("storage get_bytes failed (backend=s3): %s", type(exc).__name__)
            raise
        except Exception as exc:
            metrics.inc_storage_failure("get")
            logger.warning("storage get_bytes failed (backend=s3): %s", type(exc).__name__)
            raise
        return obj["Body"].read()
    try:
        return _local_path(key).read_bytes()
    except FileNotFoundError as exc:
        raise StorageObjectNotFoundError(key) from exc
    except Exception as exc:
        metrics.inc_storage_failure("get")
        logger.warning("storage get_bytes failed (backend=local): %s", type(exc).__name__)
        raise


def delete_key(key: str) -> None:
    """Delete a single object. Used for best-effort compensating cleanup
    (e.g. one file in a multi-file upload stored successfully before a
    later file's storage write failed) -- never raises on a missing key,
    since "already gone" is an equally successful outcome for a cleanup
    call."""
    if storage_backend() == "s3":
        try:
            get_s3_client().delete_object(Bucket=settings.s3_bucket, Key=key)
        except Exception as exc:
            # Phase L: compensating cleanup stays best-effort (the caller is
            # already handling a failure), but an object that could not be
            # removed is now visible: counted and logged (type only, never
            # the key -- see the Phase 30 storage logging rule).
            metrics.inc_storage_failure("delete")
            logger.warning("storage delete_key failed (backend=s3): %s", type(exc).__name__)
        return
    try:
        _local_path(key).unlink(missing_ok=True)
    except ValueError:
        pass


def object_exists(key: str) -> bool:
    if storage_backend() == "s3":
        try:
            get_s3_client().head_object(Bucket=settings.s3_bucket, Key=key)
            return True
        except Exception:
            return False
    return _local_path(key).exists()


def delete_prefix(prefix: str) -> None:
    if storage_backend() == "s3":
        client = get_s3_client()
        listed = client.list_objects_v2(Bucket=settings.s3_bucket, Prefix=prefix)
        keys = [{"Key": o["Key"]} for o in listed.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": keys})
        return
    target = _local_path(prefix)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def presign_get(key: str, expires: int | None = None) -> str:
    # Phase 20: neither presign_get nor presign_put is called anywhere in
    # this codebase today (every download goes through backend streaming --
    # see verifications.py/certificates.py) -- confirmed by grep, not
    # assumed. Guarded rather than removed, since it's a legitimate future
    # extension point; without the guard, calling this while running on the
    # local-filesystem dev backend would attempt to build a boto3 client
    # from empty/None S3 settings and fail with a confusing, unrelated
    # error instead of a clear one.
    if storage_backend() != "s3":
        raise RuntimeError("presign_get requires S3/MinIO storage to be configured")
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires or settings.s3_presign_expiry_seconds,
    )


def presign_put(key: str, content_type: str, expires: int | None = None) -> str:
    if storage_backend() != "s3":
        raise RuntimeError("presign_put requires S3/MinIO storage to be configured")
    return get_s3_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=expires or settings.s3_presign_expiry_seconds,
    )
