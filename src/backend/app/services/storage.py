import asyncio
import io
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any

from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.core.errors import StorageError
from app.core.logging import get_logger
from app.core.security import content_hash

log = get_logger(__name__)


@dataclass(slots=True)
class StoredObject:
    bucket: str
    key: str
    size: int
    etag: str | None
    content_hash: str


class StorageService:
    def __init__(self) -> None:
        self._client: Minio | None = None

    @property
    def client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
        return self._client

    @property
    def buckets(self) -> tuple[str, ...]:
        return (
            settings.bucket_knowledge,
            settings.bucket_exports,
            settings.bucket_templates,
            settings.bucket_avatars,
            settings.bucket_skills,
        )

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(partial(fn, *args, **kwargs))

    async def ensure_buckets(self) -> list[str]:
        created: list[str] = []
        for bucket in self.buckets:
            try:
                exists = await self._run(self.client.bucket_exists, bucket)
                if not exists:
                    await self._run(self.client.make_bucket, bucket)
                    created.append(bucket)
                    log.info("bucket_created", bucket=bucket)
            except S3Error as exc:
                if exc.code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    continue
                raise StorageError("STORAGE_503", f"Không tạo được bucket {bucket}: {exc}") from exc
            except Exception as exc:
                raise StorageError(
                    "STORAGE_503", f"Không kết nối được tới hệ thống lưu trữ: {exc}"
                ) from exc
        return created

    async def health(self) -> bool:
        try:
            await self._run(self.client.bucket_exists, settings.bucket_knowledge)
            return True
        except Exception as exc:
            log.warning("storage_unhealthy", error=str(exc))
            return False

    async def put(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        digest = content_hash(data)
        try:
            result = await self._run(
                self.client.put_object,
                bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
                metadata=metadata or {},
            )
        except S3Error as exc:
            log.warning("storage_put_failed", bucket=bucket, key=key, code=exc.code)
            raise StorageError("STORAGE_500", f"Không lưu được file: {exc.code}") from exc
        except Exception as exc:
            raise StorageError("STORAGE_503") from exc

        return StoredObject(
            bucket=bucket,
            key=key,
            size=len(data),
            etag=getattr(result, "etag", None),
            content_hash=digest,
        )

    async def get(self, bucket: str, key: str) -> bytes:
        def _read() -> bytes:
            response = self.client.get_object(bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        try:
            return await asyncio.to_thread(_read)
        except S3Error as exc:
            raise StorageError("STORAGE_500", f"Không đọc được file: {exc.code}") from exc
        except Exception as exc:
            raise StorageError("STORAGE_503") from exc

    async def delete(self, bucket: str, key: str) -> None:
        try:
            await self._run(self.client.remove_object, bucket, key)
        except Exception as exc:
            log.warning("storage_delete_failed", bucket=bucket, key=key, error=str(exc))

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Xoá mọi object dưới một prefix. Một template giữ nhiều key (file gốc, bìa,
        preview, cache ảnh từng trang) nên xoá lẻ từng key là sót."""

        def _remove() -> int:
            count = 0
            for obj in self.client.list_objects(bucket, prefix=prefix, recursive=True):
                name = obj.object_name
                if name:
                    self.client.remove_object(bucket, name)
                    count += 1
            return count

        try:
            return await asyncio.to_thread(_remove)
        except Exception as exc:
            log.warning(
                "storage_prefix_delete_failed", bucket=bucket, prefix=prefix, error=str(exc)
            )
            return 0

    async def presigned_url(
        self,
        bucket: str,
        key: str,
        expires_seconds: int | None = None,
        download_name: str | None = None,
    ) -> str:
        ttl = timedelta(seconds=expires_seconds or settings.presigned_url_ttl_seconds)
        params = (
            {"response-content-disposition": f'attachment; filename="{download_name}"'}
            if download_name
            else None
        )
        try:
            return await self._run(
                self.client.presigned_get_object,
                bucket,
                key,
                expires=ttl,
                response_headers=params,
            )
        except Exception as exc:
            raise StorageError("STORAGE_503", f"Không tạo được link tải: {exc}") from exc

    async def exists(self, bucket: str, key: str) -> bool:
        try:
            await self._run(self.client.stat_object, bucket, key)
            return True
        except Exception:
            return False

    @staticmethod
    def knowledge_key(kb_id: str, file_id: str, filename: str) -> str:
        return f"kb/{kb_id}/{file_id}/{filename}"

    @staticmethod
    def export_key(proposal_id: str, export_id: str, filename: str) -> str:
        return f"exports/{proposal_id}/{export_id}/{filename}"

    @staticmethod
    def template_key(template_id: str, filename: str) -> str:
        return f"templates/{template_id}/{filename}"

    @staticmethod
    def avatar_key(user_id: str, filename: str) -> str:
        return f"avatars/{user_id}/{filename}"

    @staticmethod
    def skill_key(skill_id: str, path: str) -> str:
        return f"skills/{skill_id}/{path.lstrip('/')}"


storage = StorageService()
