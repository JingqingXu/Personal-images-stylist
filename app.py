import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request

from flask import Flask, g, has_request_context, jsonify, render_template, request, send_from_directory
from PIL import Image, ImageOps, UnidentifiedImageError
import yaml
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SESSION_DIR = DATA_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)


def load_local_env(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key:
            os.environ.setdefault(key, value)


load_local_env(BASE_DIR / ".env")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def read_int_env(name: str, default: int, min_value: Optional[int] = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    if min_value is not None and value < min_value:
        return min_value
    return value


def read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def read_float_env(name: str, default: float, min_value: Optional[float] = None) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        value = default
    if min_value is not None and value < min_value:
        return min_value
    return value


MAX_UPLOAD_MB = read_int_env("MAX_UPLOAD_MB", 10, min_value=1)
SESSION_TTL_HOURS = read_int_env("SESSION_TTL_HOURS", 24, min_value=0)
SESSION_CLEANUP_INTERVAL_SECONDS = read_int_env("SESSION_CLEANUP_INTERVAL_SECONDS", 600, min_value=10)
FLASK_DEBUG = read_bool_env("FLASK_DEBUG", default=False)
LAST_CLEANUP_MONOTONIC = 0.0

QWEN_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_MODEL = "qwen-vl-max-latest"
TRYON_API_URL = ""
TRYON_MODEL = "virtual-try-on"
MODEL_CONFIG_PATH_ENV = "MODEL_CONFIG_PATH"
MODEL_CONFIG_DEFAULT_PATH = BASE_DIR / "config" / "model_providers.yaml"
MODEL_CONFIG_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "data": None}
SYSTEM_PROMPT_CONFIG_PATH_ENV = "SYSTEM_PROMPT_CONFIG_PATH"
SYSTEM_PROMPT_CONFIG_DEFAULT_PATH = BASE_DIR / "config" / "system_prompts.yaml"
SYSTEM_PROMPT_CONFIG_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "data": None}
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO").strip().upper() or "INFO"
APP_LOG_MAX_BYTES = read_int_env("APP_LOG_MAX_BYTES", 10 * 1024 * 1024, min_value=1024)
APP_LOG_BACKUP_COUNT = read_int_env("APP_LOG_BACKUP_COUNT", 5, min_value=1)
LOG_MAX_FIELD_CHARS = read_int_env("LOG_MAX_FIELD_CHARS", 4000, min_value=128)
LOG_INCLUDE_IMAGE_BASE64 = read_bool_env("LOG_INCLUDE_IMAGE_BASE64", default=False)

# 默认提示词模板：可通过 config/system_prompts.yaml 覆盖
DEFAULT_STYLE_PROMPT_TEMPLATE = """
你是一名高级个人形象顾问。请根据用户提供的人像照片，输出简洁、可执行的中文建议。
请按以下结构回复：
1) 形象定位（1-2句）
2) 推荐色彩（3-5个关键词）
3) 三套穿搭建议（每套包含上装/下装/鞋/配饰）
4) 发型与妆容要点（3条）
5) 避雷建议（3条）
6) 为了更准确换装，用户下一步应上传的服装照片要求（可上传上衣、裤子、鞋子中的1-3张；分别说明角度、光线、背景）
要求：
- 语言简洁直接
- 不要输出免责声明
- 不要使用 JSON
""".strip()

DEFAULT_TRYON_PROMPT_TEMPLATE = (
    "请基于用户人像与用户上传的服装图片进行自然换装。"
    "用户可能只上传上衣、裤子、鞋子中的部分图片，也可能全部上传。"
    "保持用户面部与体型特征，让服装贴合真实比例与褶皱，输出写实结果图。"
)

OUTFIT_FIELDS = {
    "top": "outfit_top",
    "bottom": "outfit_bottom",
    "shoes": "outfit_shoes",
}

TRYON_TASK_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
VTON_OUTPUT_DIR_LABEL = (os.getenv("VTON_OUTPUT_DIR", "outputs/api").strip() or "outputs/api").replace("\\", "/").strip("/")
VTON_OUTPUT_DIR = (BASE_DIR / VTON_OUTPUT_DIR_LABEL).resolve()
VTON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
VTON_WEIGHTS_DIR = os.getenv("VTON_WEIGHTS_DIR", "weights").strip() or "weights"
VTON_DEVICE = os.getenv("VTON_DEVICE", "").strip()
FASHN_VTON_ROOT = Path(os.getenv("FASHN_VTON_ROOT", "/mnt/16t/fashn-vton-1.5")).resolve()
FASHN_VTON_API_BASE_URL = (os.getenv("FASHN_VTON_API_BASE_URL", "http://127.0.0.1:8000").strip() or "http://127.0.0.1:8000").rstrip("/")
FASHN_VTON_POLL_INTERVAL_SECONDS = read_float_env("FASHN_VTON_POLL_INTERVAL_SECONDS", 1.0, min_value=0.2)
FASHN_VTON_TASK_TIMEOUT_SECONDS = read_int_env("FASHN_VTON_TASK_TIMEOUT_SECONDS", 900, min_value=10)

TRYON_TASKS: Dict[str, Dict[str, Any]] = {}
TRYON_PENDING_TASK_IDS: deque[str] = deque()
TRYON_ACTIVE_TASK_ID: Optional[str] = None
TRYON_TASK_LOCK = threading.Lock()
TRYON_TASK_CONDITION = threading.Condition(TRYON_TASK_LOCK)
TRYON_WORKER_THREAD: Optional[threading.Thread] = None

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["JSON_AS_ASCII"] = False

AUDIT_LOGGER = logging.getLogger("personal_image_stylist.audit")
MODEL_LOGGER = logging.getLogger("personal_image_stylist.model")


def resolve_log_path(env_name: str, default_path: Path) -> Path:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default_path
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def setup_file_logger(logger: logging.Logger, path: Path, level_name: str) -> None:
    if logger.handlers:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(path),
        maxBytes=APP_LOG_MAX_BYTES,
        backupCount=APP_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)
    logger.addHandler(handler)
    logger.propagate = False


def current_request_id() -> str:
    if has_request_context():
        request_id = getattr(g, "request_id", "")
        if isinstance(request_id, str):
            return request_id
    return ""


def mask_secret(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-2:]}"


def maybe_mask_auth_header(value: str) -> str:
    stripped = value.strip()
    if stripped.lower().startswith("bearer "):
        token = stripped[7:].strip()
        return f"Bearer {mask_secret(token)}"
    return mask_secret(stripped)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in ("authorization", "api_key", "token", "secret", "password"))


def maybe_truncate_text(text: str) -> str:
    if len(text) <= LOG_MAX_FIELD_CHARS:
        return text
    extra = len(text) - LOG_MAX_FIELD_CHARS
    return f"{text[:LOG_MAX_FIELD_CHARS]}...(truncated {extra} chars)"


def summarize_image_data(value: str) -> Dict[str, Any]:
    if value.startswith("data:") and ";base64," in value:
        prefix, raw_base64 = value.split(",", 1)
        mime = prefix[5:].split(";", 1)[0] if prefix.startswith("data:") else "application/octet-stream"
        sha256 = hashlib.sha256(raw_base64.encode("utf-8")).hexdigest()[:16]
        summary: Dict[str, Any] = {
            "type": "data_url",
            "mime": mime,
            "base64_chars": len(raw_base64),
            "sha256_prefix": sha256,
        }
        if LOG_INCLUDE_IMAGE_BASE64:
            summary["data_url"] = maybe_truncate_text(value)
        return summary

    sha256 = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    summary = {
        "type": "raw_base64",
        "base64_chars": len(value),
        "sha256_prefix": sha256,
    }
    if LOG_INCLUDE_IMAGE_BASE64:
        summary["base64"] = maybe_truncate_text(value)
    return summary


def path_has_image_hint(path: tuple[str, ...]) -> bool:
    return any(("image" in item.lower() or "photo" in item.lower()) for item in path)


def normalize_log_value(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            item_path = path + (key_text,)
            if is_sensitive_key(key_text):
                if isinstance(item, str) and key_text.lower() == "authorization":
                    normalized[key_text] = maybe_mask_auth_header(item)
                else:
                    normalized[key_text] = "<masked>"
                continue
            normalized[key_text] = normalize_log_value(item, item_path)
        return normalized

    if isinstance(value, list):
        return [normalize_log_value(item, path + ("[]",)) for item in value]

    if isinstance(value, tuple):
        return [normalize_log_value(item, path + ("()",)) for item in value]

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}

    if isinstance(value, Exception):
        return maybe_truncate_text(str(value))

    if isinstance(value, str):
        lowered = value.lower()
        last_key = path[-1].lower() if path else ""
        if value.startswith("data:") and ";base64," in value:
            return summarize_image_data(value)
        if path_has_image_hint(path) and not lowered.startswith("http") and len(value) > 100:
            if re.fullmatch(r"[A-Za-z0-9+/=\s]+", value):
                return summarize_image_data(value)
        if last_key == "authorization":
            return maybe_mask_auth_header(value)
        return maybe_truncate_text(value)

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return maybe_truncate_text(repr(value))


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **kwargs: Any) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    payload: Dict[str, Any] = {
        "time": timestamp,
        "event": event,
    }
    request_id = current_request_id()
    if request_id:
        payload["request_id"] = request_id
    for key, value in kwargs.items():
        payload[key] = normalize_log_value(value, (str(key),))
    logger.log(level, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def setup_logging() -> None:
    audit_log_path = resolve_log_path("APP_LOG_PATH", LOG_DIR / "app.log")
    model_log_path = resolve_log_path("MODEL_LOG_PATH", LOG_DIR / "model_calls.log")
    setup_file_logger(AUDIT_LOGGER, audit_log_path, APP_LOG_LEVEL)
    setup_file_logger(MODEL_LOGGER, model_log_path, APP_LOG_LEVEL)


setup_logging()


class TryOnError(Exception):
    pass


def infer_vton_device() -> str:
    if VTON_DEVICE:
        return VTON_DEVICE
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def create_tryon_task_id() -> str:
    return uuid.uuid4().hex[:12]


def valid_tryon_task_id(task_id: str) -> bool:
    return bool(TRYON_TASK_ID_PATTERN.fullmatch(task_id))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_session_token() -> str:
    return secrets.token_urlsafe(24)


def valid_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_PATTERN.fullmatch(session_id))


def parse_iso_datetime(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def session_touch_time(meta: Dict[str, Any], fallback_dir: Path) -> datetime:
    for key in ("updated_at", "created_at"):
        value = meta.get(key)
        if isinstance(value, str):
            parsed = parse_iso_datetime(value)
            if parsed is not None:
                return parsed
    return datetime.fromtimestamp(fallback_dir.stat().st_mtime, tz=timezone.utc)


def cleanup_expired_sessions() -> None:
    if SESSION_TTL_HOURS <= 0:
        return
    cutoff = now_utc() - timedelta(hours=SESSION_TTL_HOURS)
    for child in SESSION_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            meta = read_session_meta(child.name)
            touched_at = session_touch_time(meta, child)
        except Exception:
            touched_at = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        if touched_at < cutoff:
            shutil.rmtree(child, ignore_errors=True)


def maybe_run_session_cleanup() -> None:
    global LAST_CLEANUP_MONOTONIC
    now_monotonic = time.monotonic()
    if now_monotonic - LAST_CLEANUP_MONOTONIC < SESSION_CLEANUP_INTERVAL_SECONDS:
        return
    LAST_CLEANUP_MONOTONIC = now_monotonic
    try:
        cleanup_expired_sessions()
    except Exception:
        app.logger.exception("Failed to cleanup expired sessions")


def verify_image_file(image_path: Path) -> None:
    try:
        with Image.open(image_path) as img:
            img.verify()
        with Image.open(image_path) as img:
            img.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("上传文件内容不是有效图片") from exc


def data_url_from_image(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int = 120,
    log_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    started_at = time.perf_counter()
    if log_context:
        log_event(
            MODEL_LOGGER,
            "model_http_request",
            endpoint=url,
            timeout_seconds=timeout,
            headers=headers,
            payload=payload,
            context=log_context,
        )

    req = url_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with url_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            response_json = json.loads(body)
            if log_context:
                log_event(
                    MODEL_LOGGER,
                    "model_http_response",
                    endpoint=url,
                    status_code=resp.getcode(),
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    response=response_json,
                    context=log_context,
                )
            return response_json
    except url_error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace").strip()
            if raw:
                detail = " ".join(raw.split())[:500]
        except Exception:
            pass
        reason = str(exc.reason or "").strip()
        suffix = f": {reason}" if reason else ""
        if log_context:
            log_event(
                MODEL_LOGGER,
                "model_http_error",
                level=logging.ERROR,
                endpoint=url,
                status_code=exc.code,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error=f"HTTP Error {exc.code}{suffix}",
                response=detail,
                context=log_context,
            )
        if detail:
            raise ValueError(f"HTTP Error {exc.code}{suffix} | response={detail}") from exc
        raise ValueError(f"HTTP Error {exc.code}{suffix}") from exc
    except (url_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        if log_context:
            log_event(
                MODEL_LOGGER,
                "model_http_exception",
                level=logging.ERROR,
                endpoint=url,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error=str(exc),
                context=log_context,
            )
        raise


def get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 120,
    log_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    request_headers = dict(headers or {})
    started_at = time.perf_counter()
    if log_context:
        log_event(
            MODEL_LOGGER,
            "model_http_get_request",
            endpoint=url,
            timeout_seconds=timeout,
            headers=request_headers,
            context=log_context,
        )
    req = url_request.Request(url, headers=request_headers, method="GET")
    try:
        with url_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            response_json = json.loads(body)
            if log_context:
                log_event(
                    MODEL_LOGGER,
                    "model_http_get_response",
                    endpoint=url,
                    status_code=resp.getcode(),
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    response=response_json,
                    context=log_context,
                )
            return response_json
    except url_error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace").strip()
            if raw:
                detail = " ".join(raw.split())[:500]
        except Exception:
            pass
        reason = str(exc.reason or "").strip()
        suffix = f": {reason}" if reason else ""
        if log_context:
            log_event(
                MODEL_LOGGER,
                "model_http_get_error",
                level=logging.ERROR,
                endpoint=url,
                status_code=exc.code,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error=f"HTTP Error {exc.code}{suffix}",
                response=detail,
                context=log_context,
            )
        if detail:
            raise ValueError(f"HTTP Error {exc.code}{suffix} | response={detail}") from exc
        raise ValueError(f"HTTP Error {exc.code}{suffix}") from exc
    except (url_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        if log_context:
            log_event(
                MODEL_LOGGER,
                "model_http_get_exception",
                level=logging.ERROR,
                endpoint=url,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error=str(exc),
                context=log_context,
            )
        raise


def guess_image_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    return "application/octet-stream"


def encode_multipart_form_data(fields: Dict[str, str], files: Dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"----codexboundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    for key, path in files.items():
        filename = path.name
        mime = guess_image_mime(path)
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode("utf-8")
        )
        chunks.append(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)
    return body, f"multipart/form-data; boundary={boundary}"


def post_multipart_json(
    url: str,
    fields: Dict[str, str],
    files: Dict[str, Path],
    timeout: int = 120,
    log_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body, content_type = encode_multipart_form_data(fields, files)
    headers = {"Content-Type": content_type}
    started_at = time.perf_counter()
    if log_context:
        log_event(
            MODEL_LOGGER,
            "model_http_multipart_request",
            endpoint=url,
            timeout_seconds=timeout,
            fields=fields,
            files={k: str(v) for k, v in files.items()},
            context=log_context,
        )
    req = url_request.Request(url, data=body, headers=headers, method="POST")
    try:
        with url_request.urlopen(req, timeout=timeout) as resp:
            body_text = resp.read().decode("utf-8")
            response_json = json.loads(body_text)
            if log_context:
                log_event(
                    MODEL_LOGGER,
                    "model_http_multipart_response",
                    endpoint=url,
                    status_code=resp.getcode(),
                    elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                    response=response_json,
                    context=log_context,
                )
            return response_json
    except url_error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace").strip()
            if raw:
                detail = " ".join(raw.split())[:500]
        except Exception:
            pass
        reason = str(exc.reason or "").strip()
        suffix = f": {reason}" if reason else ""
        if log_context:
            log_event(
                MODEL_LOGGER,
                "model_http_multipart_error",
                level=logging.ERROR,
                endpoint=url,
                status_code=exc.code,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error=f"HTTP Error {exc.code}{suffix}",
                response=detail,
                context=log_context,
            )
        if detail:
            raise ValueError(f"HTTP Error {exc.code}{suffix} | response={detail}") from exc
        raise ValueError(f"HTTP Error {exc.code}{suffix}") from exc
    except (url_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        if log_context:
            log_event(
                MODEL_LOGGER,
                "model_http_multipart_exception",
                level=logging.ERROR,
                endpoint=url,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000, 2),
                error=str(exc),
                context=log_context,
            )
        raise


def extract_chat_text(response: Dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""

    message = choices[0].get("message") or {}
    content = message.get("content", "")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(item.get("text", "").strip())
        return "\n".join([c for c in chunks if c])

    return ""


def model_config_path() -> Path:
    raw = os.getenv(MODEL_CONFIG_PATH_ENV, "").strip()
    if raw:
        return Path(raw)
    return MODEL_CONFIG_DEFAULT_PATH


def default_model_config() -> Dict[str, Any]:
    return {
        "style_advice": {
            "active_provider": "dashscope",
            "providers": {
                "dashscope": {
                    "type": "openai_compatible",
                    "endpoint": QWEN_ENDPOINT,
                    "model": QWEN_MODEL,
                    "api_key_env": "DASHSCOPE_API_KEY",
                    "temperature": 0.2,
                    "require_api_key": True,
                    "auth_mode": "bearer",
                },
                "mock": {
                    "type": "mock",
                },
            },
        },
        "tryon": {
            "active_backend": "vendor_api",
            "vendor_api": {
                "active_vendor": "default_vendor",
                "vendors": {
                    "default_vendor": {
                        "type": "openai_compatible",
                        "endpoint": TRYON_API_URL,
                        "model": TRYON_MODEL,
                        "api_key_env": "TRYON_API_KEY",
                        "timeout_seconds": 180,
                        "require_api_key": True,
                        "auth_mode": "bearer",
                    }
                },
            },
            "local_model": {
                "type": "http",
                "endpoint": "http://127.0.0.1:8001/tryon",
                "model": "local-tryon",
                "timeout_seconds": 300,
                "require_api_key": False,
                "auth_mode": "bearer",
            }
        },
    }


def validate_active_provider_group(raw: Dict[str, Any], active_key: str, providers_key: str, label: str) -> None:
    active = str(raw.get(active_key) or raw.get("active") or "").strip()
    providers = raw.get(providers_key)
    if not active:
        raise ValueError(f"{label} missing {active_key}")
    if not isinstance(providers, dict) or not providers:
        raise ValueError(f"{label} missing {providers_key}")
    if active not in providers or not isinstance(providers[active], dict):
        raise ValueError(f"{label} active target not found in {providers_key}")


def normalize_model_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    style_advice = raw.get("style_advice")
    tryon = raw.get("tryon")
    if not isinstance(style_advice, dict):
        raise ValueError("missing style_advice section")
    if not isinstance(tryon, dict):
        raise ValueError("missing tryon section")

    validate_active_provider_group(style_advice, "active_provider", "providers", "style_advice")

    active_backend = str(tryon.get("active_backend") or "").strip()
    if active_backend not in {"vendor_api", "local_model"}:
        raise ValueError("tryon.active_backend must be vendor_api or local_model")

    vendor_api = tryon.get("vendor_api")
    if not isinstance(vendor_api, dict):
        raise ValueError("missing tryon.vendor_api section")
    validate_active_provider_group(vendor_api, "active_vendor", "vendors", "tryon.vendor_api")

    if not isinstance(tryon.get("local_model"), dict):
        raise ValueError("missing tryon.local_model section")

    return raw


def load_model_config() -> tuple[Dict[str, Any], str]:
    config_path = model_config_path()
    default_config = default_model_config()
    cache_path = str(config_path.resolve())
    try:
        mtime = config_path.stat().st_mtime
    except FileNotFoundError:
        return default_config, f"未找到模型配置文件：{config_path}，已使用默认厂商。"

    if (
        MODEL_CONFIG_CACHE.get("path") == cache_path
        and MODEL_CONFIG_CACHE.get("mtime") == mtime
        and isinstance(MODEL_CONFIG_CACHE.get("data"), dict)
    ):
        return MODEL_CONFIG_CACHE["data"], ""

    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("config root must be object")
        normalized = normalize_model_config(parsed)
    except Exception as exc:
        return default_config, f"读取模型配置文件失败：{exc}，已使用默认厂商。"

    MODEL_CONFIG_CACHE["path"] = cache_path
    MODEL_CONFIG_CACHE["mtime"] = mtime
    MODEL_CONFIG_CACHE["data"] = normalized
    return normalized, ""


def system_prompt_config_path() -> Path:
    raw = os.getenv(SYSTEM_PROMPT_CONFIG_PATH_ENV, "").strip()
    if raw:
        return Path(raw)
    return SYSTEM_PROMPT_CONFIG_DEFAULT_PATH


def default_system_prompt_config() -> Dict[str, Any]:
    return {
        "prompts": {
            "style_advice": DEFAULT_STYLE_PROMPT_TEMPLATE,
            "tryon": DEFAULT_TRYON_PROMPT_TEMPLATE,
        }
    }


def normalize_system_prompt_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    prompts = raw.get("prompts")
    if not isinstance(prompts, dict):
        raise ValueError("missing prompts section")

    style_advice = prompts.get("style_advice")
    tryon = prompts.get("tryon")
    if not isinstance(style_advice, str) or not style_advice.strip():
        raise ValueError("missing prompts.style_advice")
    if not isinstance(tryon, str) or not tryon.strip():
        raise ValueError("missing prompts.tryon")

    return {
        "prompts": {
            "style_advice": style_advice.strip(),
            "tryon": tryon.strip(),
        }
    }


def load_system_prompt_config() -> tuple[Dict[str, Any], str]:
    config_path = system_prompt_config_path()
    default_config = default_system_prompt_config()
    cache_path = str(config_path.resolve())
    try:
        mtime = config_path.stat().st_mtime
    except FileNotFoundError:
        return default_config, f"未找到系统提示词配置文件：{config_path}，已使用内置模板。"

    if (
        SYSTEM_PROMPT_CONFIG_CACHE.get("path") == cache_path
        and SYSTEM_PROMPT_CONFIG_CACHE.get("mtime") == mtime
        and isinstance(SYSTEM_PROMPT_CONFIG_CACHE.get("data"), dict)
    ):
        return SYSTEM_PROMPT_CONFIG_CACHE["data"], ""

    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("config root must be object")
        normalized = normalize_system_prompt_config(parsed)
    except Exception as exc:
        return default_config, f"读取系统提示词配置失败：{exc}，已使用内置模板。"

    SYSTEM_PROMPT_CONFIG_CACHE["path"] = cache_path
    SYSTEM_PROMPT_CONFIG_CACHE["mtime"] = mtime
    SYSTEM_PROMPT_CONFIG_CACHE["data"] = normalized
    return normalized, ""


def merge_notes(*notes: str) -> str:
    merged: list[str] = []
    for note in notes:
        if isinstance(note, str):
            text = note.strip()
            if text:
                merged.append(text)
    return " ".join(merged)


def load_prompt_templates() -> tuple[str, str, str]:
    config, config_note = load_system_prompt_config()
    prompts = config.get("prompts") if isinstance(config.get("prompts"), dict) else {}
    style_prompt = str(prompts.get("style_advice") or DEFAULT_STYLE_PROMPT_TEMPLATE).strip()
    tryon_prompt = str(prompts.get("tryon") or DEFAULT_TRYON_PROMPT_TEMPLATE).strip()
    return style_prompt, tryon_prompt, config_note.strip()


def as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int, min_value: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < min_value:
        return min_value
    return parsed


def as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def parse_form_int(name: str, default: int, min_value: int, max_value: Optional[int] = None) -> int:
    raw = request.form.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if value < min_value:
        raise ValueError(f"{name} 必须大于等于 {min_value}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{name} 必须小于等于 {max_value}")
    return value


def parse_form_float(name: str, default: float, min_value: Optional[float] = None) -> float:
    raw = request.form.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} 必须大于等于 {min_value}")
    return value


def parse_form_bool(name: str, default: bool) -> bool:
    raw = request.form.get(name)
    if raw is None or not str(raw).strip():
        return default
    lowered = str(raw).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值（true/false）")


def absolute_url_for(path: str) -> str:
    base_url = request.host_url.rstrip("/")
    return f"{base_url}{path}"


def resolve_api_key(provider_cfg: Dict[str, Any]) -> tuple[str, str]:
    api_key = str(provider_cfg.get("api_key", "")).strip()
    if api_key:
        return api_key, "api_key"

    env_candidates: list[str] = []
    api_key_env = provider_cfg.get("api_key_env")
    if isinstance(api_key_env, str) and api_key_env.strip():
        env_candidates.append(api_key_env.strip())

    api_key_envs = provider_cfg.get("api_key_envs")
    if isinstance(api_key_envs, list):
        for item in api_key_envs:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    env_candidates.append(name)

    seen = set()
    unique_candidates = []
    for name in env_candidates:
        if name not in seen:
            unique_candidates.append(name)
            seen.add(name)

    for name in unique_candidates:
        value = os.getenv(name, "").strip()
        if value:
            return value, name

    key_hint = "/".join(unique_candidates) if unique_candidates else "api_key"
    return "", key_hint


def fallback_advice() -> str:
    return (
        "形象定位：简洁利落的通勤休闲风，重点提升上半身层次与色彩协调。\n"
        "推荐色彩：雾霾蓝、米白、炭灰、卡其、深牛仔蓝。\n"
        "穿搭建议1：短款夹克 + 纯色T恤 + 直筒牛仔裤 + 小白鞋。\n"
        "穿搭建议2：轻薄针织开衫 + 内搭衬衫 + 九分西裤 + 乐福鞋。\n"
        "穿搭建议3：挺阔风衣 + 修身打底 + 高腰阔腿裤 + 短靴。\n"
        "发型与妆容：顶部增加蓬松度；眉眼线条更清晰；唇色选低饱和豆沙系。\n"
        "避雷建议：避免过多高饱和撞色；避免过松无版型上衣；避免鞋裤颜色完全断层。\n"
        "服装照片上传要求：可上传上衣、裤子、鞋子中的1-3张（建议尽量上传全）；每张都应平铺或正挂，正面拍摄，光线均匀，背景干净，避免遮挡与折叠。"
    )


def should_retry_style_call(exc: Exception) -> bool:
    message = str(exc).lower()
    retry_tokens = (
        "timed out",
        "timeout",
        "temporary failure in name resolution",
        "connection reset",
        "connection aborted",
        "http error 429",
        "http error 500",
        "http error 502",
        "http error 503",
        "http error 504",
    )
    return any(token in message for token in retry_tokens)


def call_qwen_style_advice(user_photo_path: Path) -> Dict[str, str]:
    config, config_note = load_model_config()
    style_prompt, _, prompt_config_note = load_prompt_templates()
    merged_config_note = merge_notes(config_note, prompt_config_note)
    style_cfg = config.get("style_advice") if isinstance(config.get("style_advice"), dict) else {}
    provider_name = style_cfg.get("active_provider", "")
    providers = style_cfg.get("providers", {})
    provider_cfg = providers.get(provider_name) if isinstance(providers, dict) else None

    if not isinstance(provider_cfg, dict):
        note = "模型配置无效，已返回本地默认建议。"
        if merged_config_note:
            note = f"{merged_config_note} {note}"
        return {"provider": "fallback", "advice": fallback_advice(), "note": note}

    provider_type = str(provider_cfg.get("type", "openai_compatible")).strip().lower()
    if provider_type == "mock":
        note = f"当前为 mock 模式（provider={provider_name}），已返回本地默认建议。"
        if merged_config_note:
            note = f"{merged_config_note} {note}"
        return {"provider": provider_name or "mock", "advice": fallback_advice(), "note": note}

    if provider_type != "openai_compatible":
        note = f"不支持的模型类型：{provider_type}，已返回本地默认建议。"
        if merged_config_note:
            note = f"{merged_config_note} {note}"
        return {"provider": "fallback", "advice": fallback_advice(), "note": note}

    endpoint = str(provider_cfg.get("endpoint", "")).strip()
    model = str(provider_cfg.get("model", "")).strip()
    if not endpoint or not model:
        note = f"provider={provider_name} 缺少 endpoint/model 配置，已返回本地默认建议。"
        if merged_config_note:
            note = f"{merged_config_note} {note}"
        return {"provider": "fallback", "advice": fallback_advice(), "note": note}

    api_key, key_hint = resolve_api_key(provider_cfg)

    require_api_key = as_bool(provider_cfg.get("require_api_key"), True)
    if not api_key and require_api_key:
        note = f"provider={provider_name} 缺少鉴权信息（{key_hint}），已返回本地默认建议。"
        if merged_config_note:
            note = f"{merged_config_note} {note}"
        return {"provider": "fallback", "advice": fallback_advice(), "note": note}

    payload = {
        "model": model,
        "temperature": as_float(provider_cfg.get("temperature", 0.2), 0.2),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": style_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url_from_image(user_photo_path)},
                    },
                ],
            }
        ],
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        auth_mode = str(provider_cfg.get("auth_mode", "bearer")).strip().lower()
        if auth_mode == "header":
            auth_header = str(provider_cfg.get("auth_header", "Authorization")).strip() or "Authorization"
            headers[auth_header] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    timeout_seconds = as_int(provider_cfg.get("timeout_seconds", 180), 180, min_value=5)
    max_retries = as_int(provider_cfg.get("max_retries", 1), 1, min_value=0)
    last_exc: Optional[Exception] = None
    model_call_id = uuid.uuid4().hex
    log_event(
        MODEL_LOGGER,
        "style_advice_start",
        provider=provider_name,
        model=model,
        endpoint=endpoint,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        user_photo_path=user_photo_path,
        model_call_id=model_call_id,
    )
    for attempt in range(max_retries + 1):
        try:
            response = post_json(
                endpoint,
                payload,
                headers,
                timeout=timeout_seconds,
                log_context={
                    "task": "style_advice",
                    "provider": provider_name,
                    "model": model,
                    "attempt": attempt + 1,
                    "max_retries": max_retries,
                    "model_call_id": model_call_id,
                },
            )
            advice = extract_chat_text(response)
            if not advice:
                raise ValueError("style model response missing message content")
            note = merged_config_note
            log_event(
                MODEL_LOGGER,
                "style_advice_success",
                provider=provider_name,
                model=model,
                advice=advice,
                note=note,
                attempt=attempt + 1,
                model_call_id=model_call_id,
            )
            return {"provider": provider_name, "advice": advice, "note": note}
        except (url_error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            last_exc = exc
            log_event(
                MODEL_LOGGER,
                "style_advice_attempt_failed",
                level=logging.WARNING,
                provider=provider_name,
                model=model,
                attempt=attempt + 1,
                max_retries=max_retries,
                error=str(exc),
                model_call_id=model_call_id,
            )
            if attempt < max_retries and should_retry_style_call(exc):
                time.sleep(1.2 * (attempt + 1))
                continue
            break

    note = f"调用模型 provider={provider_name} 失败，已回退默认建议：{last_exc}"
    if max_retries > 0:
        note += f"（已重试 {max_retries} 次）"
    if merged_config_note:
        note = f"{merged_config_note} {note}"
    log_event(
        MODEL_LOGGER,
        "style_advice_fallback",
        level=logging.ERROR,
        provider=provider_name,
        model=model,
        note=note,
        model_call_id=model_call_id,
    )
    return {"provider": "fallback", "advice": fallback_advice(), "note": note}


def deep_get(data: Dict[str, Any], keys: list[str]) -> Optional[Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def decode_b64_to_file(value: str, output_path: Path) -> None:
    raw = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    try:
        output_path.write_bytes(base64.b64decode(raw))
    except Exception as exc:
        raise TryOnError("换装模型返回的 base64 图片无法解码") from exc


def download_file(url: str, output_path: Path) -> None:
    parsed = url_parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise TryOnError("换装模型返回了不受支持的图片地址协议")
    req = url_request.Request(url, method="GET")
    try:
        with url_request.urlopen(req, timeout=120) as resp:
            output_path.write_bytes(resp.read())
    except (url_error.URLError, TimeoutError) as exc:
        raise TryOnError("下载换装结果图片失败") from exc


def build_outfit_board(outfit_paths: Dict[str, Path], output_path: Path) -> Path:
    selected_order = [key for key in ("top", "bottom", "shoes") if key in outfit_paths]
    if not selected_order:
        raise TryOnError("至少需要上传一张服装图片")

    slot_size = (384, 512)
    gap = 16
    padding = 18
    board_width = padding * 2 + slot_size[0] * len(selected_order) + gap * (len(selected_order) - 1)
    board_height = padding * 2 + slot_size[1]

    canvas = Image.new("RGB", (board_width, board_height), color=(250, 252, 255))
    for idx, key in enumerate(selected_order):
        image_path = outfit_paths[key]
        with Image.open(image_path) as img:
            fitted = ImageOps.contain(img.convert("RGB"), slot_size)
        x = padding + idx * (slot_size[0] + gap) + (slot_size[0] - fitted.width) // 2
        y = padding + (slot_size[1] - fitted.height) // 2
        canvas.paste(fitted, (x, y))

    canvas.save(output_path, format="PNG")
    return output_path


def build_tryon_input_payload(
    person_path: Path,
    outfit_paths: Dict[str, Path],
    advice: str,
    tryon_prompt: str,
) -> Dict[str, Any]:
    board_path = person_path.parent / "outfit_board.png"
    build_outfit_board(outfit_paths, board_path)
    garment_images = {key: data_url_from_image(path) for key, path in outfit_paths.items()}
    input_payload = {
        "person_image": data_url_from_image(person_path),
        "garment_image": data_url_from_image(board_path),
        "garment_images": garment_images,
        "prompt": tryon_prompt,
        "style_advice": advice,
    }
    if "top" in outfit_paths:
        input_payload["top_image"] = garment_images["top"]
    if "bottom" in outfit_paths:
        input_payload["bottom_image"] = garment_images["bottom"]
    if "shoes" in outfit_paths:
        input_payload["shoes_image"] = garment_images["shoes"]
    return input_payload


def build_provider_headers(provider_cfg: Dict[str, Any]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key, key_hint = resolve_api_key(provider_cfg)

    require_api_key = as_bool(provider_cfg.get("require_api_key"), False)
    if not api_key and require_api_key:
        raise TryOnError(f"当前模型配置缺少鉴权信息（{key_hint}）")

    if api_key:
        auth_mode = str(provider_cfg.get("auth_mode", "bearer")).strip().lower()
        if auth_mode == "header":
            auth_header = str(provider_cfg.get("auth_header", "Authorization")).strip() or "Authorization"
            headers[auth_header] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    return headers


def call_tryon_vendor_api(input_payload: Dict[str, Any], tryon_cfg: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    vendor_api_cfg = tryon_cfg.get("vendor_api")
    if not isinstance(vendor_api_cfg, dict):
        raise TryOnError("模型配置缺少 tryon.vendor_api 段")
    active_vendor = str(vendor_api_cfg.get("active_vendor") or "").strip()
    vendors = vendor_api_cfg.get("vendors")
    if not active_vendor or not isinstance(vendors, dict):
        raise TryOnError("模型配置缺少 tryon.vendor_api.active_vendor/vendors")
    vendor_cfg = vendors.get(active_vendor)
    if not isinstance(vendor_cfg, dict):
        raise TryOnError(f"未找到换装厂商配置：{active_vendor}")

    endpoint = str(vendor_cfg.get("endpoint", "")).strip()
    model_name = str(vendor_cfg.get("model", "")).strip()
    if not endpoint or not model_name:
        raise TryOnError(f"换装厂商 {active_vendor} 缺少 endpoint/model 配置")

    payload = {"model": model_name, "input": input_payload}
    headers = build_provider_headers(vendor_cfg)
    timeout_seconds = as_int(vendor_cfg.get("timeout_seconds", 180), 180, min_value=5)
    model_call_id = uuid.uuid4().hex

    try:
        response = post_json(
            endpoint,
            payload,
            headers,
            timeout=timeout_seconds,
            log_context={
                "task": "tryon_vendor_api",
                "provider": active_vendor,
                "model": model_name,
                "model_call_id": model_call_id,
            },
        )
        log_event(
            MODEL_LOGGER,
            "tryon_vendor_success",
            provider=active_vendor,
            model=model_name,
            endpoint=endpoint,
            model_call_id=model_call_id,
        )
        return response, f"vendor_api:{active_vendor}"
    except (url_error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        log_event(
            MODEL_LOGGER,
            "tryon_vendor_failed",
            level=logging.ERROR,
            provider=active_vendor,
            model=model_name,
            endpoint=endpoint,
            error=str(exc),
            model_call_id=model_call_id,
        )
        raise TryOnError(f"调用换装厂商 {active_vendor} 失败，请检查接口地址、鉴权和返回格式") from exc


def call_tryon_local_model(input_payload: Dict[str, Any], tryon_cfg: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    local_cfg = tryon_cfg.get("local_model")
    if not isinstance(local_cfg, dict):
        raise TryOnError("模型配置缺少 tryon.local_model 段")

    local_type = str(local_cfg.get("type", "http")).strip().lower()
    if local_type != "http":
        raise TryOnError(f"不支持的本地模型调用类型：{local_type}（当前仅支持 http）")

    endpoint = str(local_cfg.get("endpoint", "")).strip()
    model_name = str(local_cfg.get("model", "local-tryon")).strip()
    if not endpoint:
        raise TryOnError("本地模型配置缺少 endpoint")

    payload = {"model": model_name, "input": input_payload}
    headers = build_provider_headers(local_cfg)
    timeout_seconds = as_int(local_cfg.get("timeout_seconds", 300), 300, min_value=5)
    model_call_id = uuid.uuid4().hex

    try:
        response = post_json(
            endpoint,
            payload,
            headers,
            timeout=timeout_seconds,
            log_context={
                "task": "tryon_local_model",
                "provider": "local_model",
                "model": model_name,
                "model_call_id": model_call_id,
            },
        )
        log_event(
            MODEL_LOGGER,
            "tryon_local_success",
            provider="local_model",
            model=model_name,
            endpoint=endpoint,
            model_call_id=model_call_id,
        )
        return response, "local_model"
    except (url_error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        log_event(
            MODEL_LOGGER,
            "tryon_local_failed",
            level=logging.ERROR,
            provider="local_model",
            model=model_name,
            endpoint=endpoint,
            error=str(exc),
            model_call_id=model_call_id,
        )
        raise TryOnError("调用本地换装模型失败，请检查本地服务是否已启动") from exc


def try_call_tryon_model(person_path: Path, outfit_paths: Dict[str, Path], advice: str) -> tuple[Dict[str, Any], str, str]:
    config, config_note = load_model_config()
    _, tryon_prompt, prompt_config_note = load_prompt_templates()
    tryon_cfg = config.get("tryon") if isinstance(config.get("tryon"), dict) else {}
    active_backend = str(tryon_cfg.get("active_backend", "")).strip().lower()

    input_payload = build_tryon_input_payload(person_path, outfit_paths, advice, tryon_prompt)

    if active_backend == "vendor_api":
        response, provider_tag = call_tryon_vendor_api(input_payload, tryon_cfg)
    elif active_backend == "local_model":
        response, provider_tag = call_tryon_local_model(input_payload, tryon_cfg)
    else:
        raise TryOnError(f"不支持的换装后端类型：{active_backend}")
    return response, provider_tag, merge_notes(config_note, prompt_config_note)


def save_tryon_result(response_data: Dict[str, Any], output_path: Path) -> bool:
    base64_candidates = [
        ["output", "image_base64"],
        ["output", "result_image_base64"],
        ["result", "image_base64"],
        ["image_base64"],
    ]
    url_candidates = [
        ["output", "image_url"],
        ["output", "result_image_url"],
        ["result", "image_url"],
        ["image_url"],
    ]

    for path in base64_candidates:
        value = deep_get(response_data, path)
        if isinstance(value, str) and value.strip():
            decode_b64_to_file(value.strip(), output_path)
            verify_image_file(output_path)
            return True

    for path in url_candidates:
        value = deep_get(response_data, path)
        if isinstance(value, str) and value.strip():
            download_file(value.strip(), output_path)
            verify_image_file(output_path)
            return True

    return False


def build_tryon_result(
    user_photo_path: Path,
    outfit_paths: Dict[str, Path],
    advice: str,
    output_path: Path,
) -> Dict[str, str]:
    response_data, provider_tag, config_note = try_call_tryon_model(user_photo_path, outfit_paths, advice)
    if save_tryon_result(response_data, output_path):
        return {"provider": provider_tag, "note": config_note}
    raise TryOnError("换装模型返回中未找到可用图片字段")


def tryon_queue_position_locked(task_id: str) -> Optional[int]:
    task = TRYON_TASKS.get(task_id)
    if not isinstance(task, dict):
        return None

    status = str(task.get("status", ""))
    if status in {"completed", "failed"}:
        return None
    if TRYON_ACTIVE_TASK_ID == task_id:
        return 0

    for idx, queued_id in enumerate(TRYON_PENDING_TASK_IDS):
        if queued_id == task_id:
            return idx + (1 if TRYON_ACTIVE_TASK_ID else 0)
    return None


def tryon_task_view_locked(task: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    queue_position = tryon_queue_position_locked(task_id)
    payload: Dict[str, Any] = {
        "task_id": task_id,
        "status": task.get("status"),
        "stage": task.get("stage"),
        "progress": task.get("progress"),
        "queue_position": queue_position,
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "finished_at": task.get("finished_at"),
        "elapsed_seconds": task.get("elapsed_seconds"),
        "category": task.get("category"),
        "garment_photo_type": task.get("garment_photo_type"),
        "inputs": dict(task.get("inputs", {})),
        "outputs": [],
        "error": task.get("error"),
    }
    outputs = task.get("outputs", [])
    if isinstance(outputs, list):
        for item in outputs:
            if not isinstance(item, dict):
                continue
            url_path = item.get("url_path")
            output_item = {
                "index": item.get("index"),
                "file_path": item.get("file_path"),
                "url_path": url_path,
                "url": absolute_url_for(url_path) if isinstance(url_path, str) and url_path.startswith("/") else None,
            }
            payload["outputs"].append(output_item)
    return payload


def mark_tryon_task_failed(task_id: str, message: str) -> None:
    with TRYON_TASK_LOCK:
        task = TRYON_TASKS.get(task_id)
        if not isinstance(task, dict):
            return
        now_ts = time.time()
        started = task.get("started_at")
        elapsed = None
        if isinstance(started, (int, float)):
            elapsed = round(now_ts - started, 3)
        task["status"] = "failed"
        task["stage"] = "failed"
        task["progress"] = 100
        task["finished_at"] = now_ts
        task["elapsed_seconds"] = elapsed
        task["error"] = message
        task["queue_position"] = None


def update_tryon_task_progress(task_id: str, stage: str, progress: int) -> None:
    with TRYON_TASK_LOCK:
        task = TRYON_TASKS.get(task_id)
        if not isinstance(task, dict):
            return
        task["stage"] = stage
        task["progress"] = max(0, min(100, progress))


def build_outfit_paths_for_category(category: str, garment_path: Path) -> Dict[str, Path]:
    if category == "tops":
        return {"top": garment_path}
    if category == "bottoms":
        return {"bottom": garment_path}
    return {"top": garment_path}


def fashn_api_url(path: str) -> str:
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{FASHN_VTON_API_BASE_URL}{suffix}"


def resolve_absolute_url(base_url: str, value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"{base_url.rstrip('/')}/{value.lstrip('/')}"


def submit_fashn_tryon_task(task: Dict[str, Any], person_path: Path, garment_path: Path) -> str:
    fields = {
        "category": str(task.get("category", "tops")),
        "garment_photo_type": str(task.get("garment_photo_type", "model")),
        "num_samples": str(task.get("num_samples", 1)),
        "num_timesteps": str(task.get("num_timesteps", 30)),
        "guidance_scale": str(task.get("guidance_scale", 1.5)),
        "seed": str(task.get("seed", 42)),
        "segmentation_free": "true" if bool(task.get("segmentation_free", True)) else "false",
    }
    files = {
        "person_image": person_path,
        "garment_image": garment_path,
    }
    model_call_id = uuid.uuid4().hex
    try:
        response = post_multipart_json(
            fashn_api_url("/tryon"),
            fields=fields,
            files=files,
            timeout=min(FASHN_VTON_TASK_TIMEOUT_SECONDS, 120),
            log_context={
                "task": "fashn_vton_submit",
                "base_url": FASHN_VTON_API_BASE_URL,
                "model_call_id": model_call_id,
            },
        )
    except (url_error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise TryOnError(
            "提交到 FASHN-VTON 服务失败，请先启动 /mnt/16t/fashn-vton-1.5/examples/api_server.py"
        ) from exc

    upstream_task_id = response.get("task_id")
    if not isinstance(upstream_task_id, str) or not upstream_task_id:
        raise TryOnError("FASHN-VTON 返回缺少 task_id")
    return upstream_task_id


def poll_fashn_task_until_done(upstream_task_id: str, task_id: str) -> Dict[str, Any]:
    deadline = time.time() + FASHN_VTON_TASK_TIMEOUT_SECONDS
    model_call_id = uuid.uuid4().hex
    latest: Dict[str, Any] = {}

    while True:
        if time.time() > deadline:
            raise TryOnError(f"FASHN-VTON 任务超时（>{FASHN_VTON_TASK_TIMEOUT_SECONDS}s）")
        try:
            latest = get_json(
                fashn_api_url(f"/tasks/{upstream_task_id}"),
                timeout=min(FASHN_VTON_TASK_TIMEOUT_SECONDS, 120),
                log_context={
                    "task": "fashn_vton_poll",
                    "task_id": task_id,
                    "upstream_task_id": upstream_task_id,
                    "model_call_id": model_call_id,
                },
            )
        except (url_error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise TryOnError("轮询 FASHN-VTON 任务状态失败") from exc

        upstream_status = str(latest.get("status") or "").strip().lower()
        upstream_stage = str(latest.get("stage") or "running_inference")
        upstream_progress_raw = latest.get("progress")
        try:
            upstream_progress = int(upstream_progress_raw)
        except (TypeError, ValueError):
            upstream_progress = 0
        update_tryon_task_progress(task_id, upstream_stage, upstream_progress)

        if upstream_status == "completed":
            return latest
        if upstream_status == "failed":
            detail = latest.get("error")
            message = str(detail).strip() if detail else "FASHN-VTON 执行失败"
            raise TryOnError(message)

        time.sleep(FASHN_VTON_POLL_INTERVAL_SECONDS)


def sync_fashn_outputs_to_local(
    task_id: str,
    task_dir: Path,
    upstream_task_response: Dict[str, Any],
) -> list[Dict[str, Any]]:
    outputs = upstream_task_response.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise TryOnError("FASHN-VTON 未返回输出图片")

    update_tryon_task_progress(task_id, "saving_outputs", 90)
    local_outputs: list[Dict[str, Any]] = []
    for idx, output_item in enumerate(outputs):
        if not isinstance(output_item, dict):
            continue
        source_url = None
        url_value = output_item.get("url")
        if isinstance(url_value, str) and url_value.strip():
            source_url = url_value.strip()
        else:
            url_path_value = output_item.get("url_path")
            if isinstance(url_path_value, str) and url_path_value.strip():
                source_url = resolve_absolute_url(FASHN_VTON_API_BASE_URL, url_path_value.strip())
        if not source_url:
            continue

        output_name = f"output_{idx:02d}.png"
        output_path = task_dir / output_name
        download_file(source_url, output_path)
        verify_image_file(output_path)
        local_outputs.append(
            {
                "index": idx,
                "file_path": f"{VTON_OUTPUT_DIR_LABEL}/{task_id}/{output_name}",
                "url_path": f"/outputs/{task_id}/{output_name}",
            }
        )

    if not local_outputs:
        raise TryOnError("FASHN-VTON 输出结果不可用")
    return local_outputs


def infer_tryon_category_from_outfits(outfit_paths: Dict[str, Path]) -> str:
    keys = set(outfit_paths.keys())
    if keys == {"bottom"}:
        return "bottoms"
    if keys == {"top"}:
        return "tops"
    if keys <= {"bottom", "shoes"} and "bottom" in keys and "top" not in keys:
        return "bottoms"
    if "top" in keys and "bottom" not in keys:
        return "tops"
    return "one-pieces"


def resolve_fashn_garment_image_for_session_tryon(session_dir: Path, outfit_paths: Dict[str, Path]) -> Path:
    if len(outfit_paths) == 1:
        single_path = next(iter(outfit_paths.values()))
        if single_path.exists():
            return single_path

    board_path = session_dir / "garment_composed.png"
    build_outfit_board(outfit_paths, board_path)
    verify_image_file(board_path)
    return board_path


def download_first_fashn_output(upstream_task_response: Dict[str, Any], output_path: Path) -> None:
    outputs = upstream_task_response.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise TryOnError("FASHN-VTON 未返回输出图片")

    for output_item in outputs:
        if not isinstance(output_item, dict):
            continue
        source_url = None
        url_value = output_item.get("url")
        if isinstance(url_value, str) and url_value.strip():
            source_url = url_value.strip()
        else:
            url_path_value = output_item.get("url_path")
            if isinstance(url_path_value, str) and url_path_value.strip():
                source_url = resolve_absolute_url(FASHN_VTON_API_BASE_URL, url_path_value.strip())
        if not source_url:
            continue

        download_file(source_url, output_path)
        verify_image_file(output_path)
        return

    raise TryOnError("FASHN-VTON 输出结果不可用")


def run_session_tryon_via_fashn(
    session_id: str,
    user_photo_path: Path,
    outfit_paths: Dict[str, Path],
    result_path: Path,
) -> Dict[str, str]:
    if not outfit_paths:
        raise TryOnError("至少需要上传一张服装图片")
    if not user_photo_path.exists():
        raise TryOnError("会话原始照片不存在，请重新上传")

    session_dir = user_photo_path.parent
    category = infer_tryon_category_from_outfits(outfit_paths)
    garment_image_path = resolve_fashn_garment_image_for_session_tryon(session_dir, outfit_paths)
    task_payload = {
        "category": category,
        "garment_photo_type": "model",
        "num_samples": 1,
        "num_timesteps": 30,
        "guidance_scale": 1.5,
        "seed": 42,
        "segmentation_free": True,
    }

    upstream_task_id = submit_fashn_tryon_task(task_payload, user_photo_path, garment_image_path)
    upstream_result = poll_fashn_task_until_done(upstream_task_id, f"api-{session_id[:12]}")
    download_first_fashn_output(upstream_result, result_path)
    return {
        "provider": "fashn_vton",
        "note": f"source={FASHN_VTON_API_BASE_URL} task_id={upstream_task_id}",
    }


def run_tryon_task(task_id: str) -> None:
    with TRYON_TASK_LOCK:
        task = TRYON_TASKS.get(task_id)
        if not isinstance(task, dict):
            return
        person_path = Path(str(task.get("_person_path", "")))
        garment_path = Path(str(task.get("_garment_path", "")))
        task_dir = Path(str(task.get("_task_dir", "")))

    if not person_path.exists() or not garment_path.exists():
        mark_tryon_task_failed(task_id, "任务输入图片不存在")
        return

    update_tryon_task_progress(task_id, "loading_images", 10)
    try:
        upstream_task_id = submit_fashn_tryon_task(task, person_path, garment_path)
        update_tryon_task_progress(task_id, "queued", 15)
        upstream_result = poll_fashn_task_until_done(upstream_task_id, task_id)
        outputs = sync_fashn_outputs_to_local(task_id, task_dir, upstream_result)
    except TryOnError as exc:
        mark_tryon_task_failed(task_id, str(exc))
        return

    with TRYON_TASK_LOCK:
        task = TRYON_TASKS.get(task_id)
        if not isinstance(task, dict):
            return
        now_ts = time.time()
        started = task.get("started_at")
        elapsed = None
        if isinstance(started, (int, float)):
            elapsed = round(now_ts - started, 3)
        task["status"] = "completed"
        task["stage"] = "completed"
        task["progress"] = 100
        task["outputs"] = outputs
        task["finished_at"] = now_ts
        task["elapsed_seconds"] = elapsed
        task["error"] = None
        task["queue_position"] = None


def tryon_worker_loop() -> None:
    global TRYON_ACTIVE_TASK_ID
    while True:
        with TRYON_TASK_CONDITION:
            while not TRYON_PENDING_TASK_IDS:
                TRYON_TASK_CONDITION.wait()
            task_id = TRYON_PENDING_TASK_IDS.popleft()
            task = TRYON_TASKS.get(task_id)
            if not isinstance(task, dict):
                continue
            TRYON_ACTIVE_TASK_ID = task_id
            task["status"] = "running"
            task["stage"] = "queued"
            task["progress"] = 0
            task["started_at"] = time.time()

        try:
            run_tryon_task(task_id)
        except Exception as exc:
            mark_tryon_task_failed(task_id, f"任务执行失败：{exc}")
        finally:
            with TRYON_TASK_CONDITION:
                if TRYON_ACTIVE_TASK_ID == task_id:
                    TRYON_ACTIVE_TASK_ID = None
                TRYON_TASK_CONDITION.notify_all()


def ensure_tryon_worker_started() -> None:
    global TRYON_WORKER_THREAD
    with TRYON_TASK_LOCK:
        if TRYON_WORKER_THREAD is not None and TRYON_WORKER_THREAD.is_alive():
            return
        TRYON_WORKER_THREAD = threading.Thread(
            target=tryon_worker_loop,
            name="tryon-task-worker",
            daemon=True,
        )
        TRYON_WORKER_THREAD.start()


def allowed_image(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    return suffix in ALLOWED_EXTENSIONS


def save_uploaded_image(file_storage: Any, target_dir: Path, stem: str) -> str:
    filename = secure_filename(file_storage.filename or "")
    if not filename:
        raise ValueError("文件名为空")

    if not allowed_image(filename):
        raise ValueError("仅支持 jpg/jpeg/png/webp")

    suffix = Path(filename).suffix.lower()
    target_name = f"{stem}{suffix}"
    file_path = target_dir / target_name
    file_storage.save(str(file_path))
    try:
        verify_image_file(file_path)
    except ValueError:
        file_path.unlink(missing_ok=True)
        raise
    return target_name


def session_meta_path(session_id: str) -> Path:
    return SESSION_DIR / session_id / "meta.json"


def read_session_meta(session_id: str) -> Dict[str, Any]:
    path = session_meta_path(session_id)
    if not path.exists():
        raise FileNotFoundError("会话不存在")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("会话元数据损坏")
    return data


def write_session_meta(session_id: str, meta: Dict[str, Any]) -> None:
    path = session_meta_path(session_id)
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def verify_session_token(meta: Dict[str, Any], provided_token: str) -> bool:
    expected = meta.get("session_token")
    return isinstance(expected, str) and expected and secrets.compare_digest(expected, provided_token)


def session_public_files(meta: Dict[str, Any]) -> set[str]:
    candidates = {
        meta.get("user_photo"),
        meta.get("outfit_top"),
        meta.get("outfit_bottom"),
        meta.get("outfit_shoes"),
        meta.get("result_photo"),
    }
    return {name for name in candidates if isinstance(name, str) and name}


@app.before_request
def before_every_request() -> None:
    incoming_request_id = (request.headers.get("X-Request-ID") or "").strip()
    g.request_id = incoming_request_id[:64] if incoming_request_id else uuid.uuid4().hex[:16]
    g.request_started_at = time.perf_counter()
    maybe_run_session_cleanup()
    if request.path.startswith("/api/"):
        log_event(
            AUDIT_LOGGER,
            "http_request_start",
            method=request.method,
            path=request.path,
            query=dict(request.args),
            remote_addr=request.remote_addr,
            user_agent=request.headers.get("User-Agent", ""),
        )


@app.after_request
def after_every_request(response: Any) -> Any:
    request_id = current_request_id()
    if request_id:
        response.headers["X-Request-ID"] = request_id
    if request.path.startswith("/api/"):
        started_at = getattr(g, "request_started_at", None)
        elapsed_ms = None
        if isinstance(started_at, (int, float)):
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        log_event(
            AUDIT_LOGGER,
            "http_request_end",
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
        )
    return response


@app.route("/", methods=["GET"])
def index() -> Any:
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health_vton() -> Any:
    with TRYON_TASK_LOCK:
        active_task_id = TRYON_ACTIVE_TASK_ID
        pending_tasks = len(TRYON_PENDING_TASK_IDS)
    return jsonify(
        {
            "status": "ok",
            "weights_dir": VTON_WEIGHTS_DIR,
            "output_dir": VTON_OUTPUT_DIR_LABEL,
            "device": infer_vton_device(),
            "active_task_id": active_task_id,
            "pending_tasks": pending_tasks,
            "upstream_api_base_url": FASHN_VTON_API_BASE_URL,
            "upstream_root": str(FASHN_VTON_ROOT),
        }
    )


@app.route("/queue", methods=["GET"])
def queue_vton() -> Any:
    with TRYON_TASK_LOCK:
        return jsonify(
            {
                "active_task_id": TRYON_ACTIVE_TASK_ID,
                "pending_count": len(TRYON_PENDING_TASK_IDS),
                "pending_task_ids": list(TRYON_PENDING_TASK_IDS),
            }
        )


@app.route("/outputs/<task_id>/<filename>", methods=["GET"])
def output_file(task_id: str, filename: str) -> Any:
    if not valid_tryon_task_id(task_id):
        return jsonify({"error": "任务不存在"}), 404
    return send_from_directory(VTON_OUTPUT_DIR / task_id, filename)


@app.route("/tryon", methods=["POST"])
def submit_tryon_task() -> Any:
    ensure_tryon_worker_started()
    person_image = request.files.get("person_image")
    garment_image = request.files.get("garment_image")
    if person_image is None:
        return jsonify({"error": "缺少 person_image 文件"}), 400
    if garment_image is None:
        return jsonify({"error": "缺少 garment_image 文件"}), 400

    category = str(request.form.get("category", "")).strip()
    if category not in {"tops", "bottoms", "one-pieces"}:
        return jsonify({"error": "category 必须是 tops/bottoms/one-pieces"}), 400

    garment_photo_type = str(request.form.get("garment_photo_type", "model")).strip() or "model"
    if garment_photo_type not in {"model", "flat-lay"}:
        return jsonify({"error": "garment_photo_type 必须是 model/flat-lay"}), 400

    try:
        num_samples = parse_form_int("num_samples", default=1, min_value=1, max_value=4)
        num_timesteps = parse_form_int("num_timesteps", default=30, min_value=1)
        guidance_scale = parse_form_float("guidance_scale", default=1.5, min_value=0.0)
        seed = parse_form_int("seed", default=42, min_value=-2147483648)
        segmentation_free = parse_form_bool("segmentation_free", default=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    task_id = create_tryon_task_id()
    task_dir = VTON_OUTPUT_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    try:
        person_name = save_uploaded_image(person_image, task_dir, "person")
        garment_name = save_uploaded_image(garment_image, task_dir, "garment")
    except ValueError as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        return jsonify({"error": str(exc)}), 400

    now_ts = time.time()
    task_record: Dict[str, Any] = {
        "task_id": task_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "queue_position": None,
        "created_at": now_ts,
        "started_at": None,
        "finished_at": None,
        "elapsed_seconds": None,
        "category": category,
        "garment_photo_type": garment_photo_type,
        "num_samples": num_samples,
        "num_timesteps": num_timesteps,
        "guidance_scale": guidance_scale,
        "seed": seed,
        "segmentation_free": segmentation_free,
        "inputs": {
            "person_image": f"{VTON_OUTPUT_DIR_LABEL}/{task_id}/{person_name}",
            "garment_image": f"{VTON_OUTPUT_DIR_LABEL}/{task_id}/{garment_name}",
        },
        "outputs": [],
        "error": None,
        "_task_dir": str(task_dir),
        "_person_path": str(task_dir / person_name),
        "_garment_path": str(task_dir / garment_name),
    }

    with TRYON_TASK_CONDITION:
        TRYON_TASKS[task_id] = task_record
        TRYON_PENDING_TASK_IDS.append(task_id)
        queue_position = tryon_queue_position_locked(task_id)
        task_record["queue_position"] = queue_position
        TRYON_TASK_CONDITION.notify()

    return jsonify(
        {
            "message": "task accepted",
            "task_id": task_id,
            "status": "queued",
            "progress": 0,
            "queue_position": queue_position,
            "task_status_url": absolute_url_for(f"/tasks/{task_id}"),
        }
    )


@app.route("/tasks/<task_id>", methods=["GET"])
def get_tryon_task(task_id: str) -> Any:
    if not valid_tryon_task_id(task_id):
        return jsonify({"error": "任务不存在"}), 404
    with TRYON_TASK_LOCK:
        task = TRYON_TASKS.get(task_id)
        if not isinstance(task, dict):
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(tryon_task_view_locked(task, task_id))


@app.route("/api/health", methods=["GET"])
def health() -> Any:
    return jsonify({"status": "ok", "time": now_iso()})


@app.route("/api/files/<session_id>/<filename>", methods=["GET"])
def session_file(session_id: str, filename: str) -> Any:
    if not valid_session_id(session_id):
        return jsonify({"error": "文件不存在"}), 404

    token = (request.args.get("token") or "").strip()
    if not token:
        return jsonify({"error": "缺少访问凭证"}), 403

    try:
        meta = read_session_meta(session_id)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return jsonify({"error": "文件不存在"}), 404

    if not verify_session_token(meta, token):
        return jsonify({"error": "访问凭证无效"}), 403

    allowed_names = session_public_files(meta)
    if filename not in allowed_names:
        return jsonify({"error": "文件不存在"}), 404

    directory = SESSION_DIR / session_id
    return send_from_directory(directory, filename)


@app.route("/api/analyze", methods=["POST"])
def analyze() -> Any:
    log_event(AUDIT_LOGGER, "analyze_start")
    user_photo = request.files.get("user_photo")
    if user_photo is None:
        log_event(AUDIT_LOGGER, "analyze_invalid_request", level=logging.WARNING, reason="missing_user_photo")
        return jsonify({"error": "缺少 user_photo 文件"}), 400

    session_id = uuid.uuid4().hex
    current_session_dir = SESSION_DIR / session_id
    current_session_dir.mkdir(parents=True, exist_ok=True)

    try:
        user_photo_name = save_uploaded_image(user_photo, current_session_dir, "user_photo")
    except ValueError as exc:
        log_event(
            AUDIT_LOGGER,
            "analyze_invalid_request",
            level=logging.WARNING,
            reason="invalid_user_photo",
            error=str(exc),
        )
        return jsonify({"error": str(exc)}), 400

    user_photo_path = current_session_dir / user_photo_name
    session_token = create_session_token()

    advice_result = call_qwen_style_advice(user_photo_path)

    meta = {
        "session_id": session_id,
        "session_token": session_token,
        "created_at": now_iso(),
        "user_photo": user_photo_name,
        "advice": advice_result["advice"],
        "advice_provider": advice_result["provider"],
        "advice_note": advice_result["note"],
    }
    write_session_meta(session_id, meta)
    log_event(
        AUDIT_LOGGER,
        "analyze_success",
        session_id=session_id,
        user_photo=user_photo_name,
        user_photo_size=user_photo_path.stat().st_size if user_photo_path.exists() else None,
        advice_provider=advice_result["provider"],
        advice_note=advice_result["note"],
    )

    return jsonify(
        {
            "session_id": session_id,
            "session_token": session_token,
            "advice": advice_result["advice"],
            "advice_provider": advice_result["provider"],
            "note": advice_result["note"],
            "user_photo_url": f"/api/files/{session_id}/{user_photo_name}?token={session_token}",
        }
    )


@app.route("/api/try-on", methods=["POST"])
def try_on() -> Any:
    log_event(AUDIT_LOGGER, "tryon_start")
    session_id = (request.form.get("session_id") or "").strip()
    session_token = (request.form.get("session_token") or "").strip()
    uploaded = {
        "top": request.files.get(OUTFIT_FIELDS["top"]),
        "bottom": request.files.get(OUTFIT_FIELDS["bottom"]),
        "shoes": request.files.get(OUTFIT_FIELDS["shoes"]),
    }

    if not session_id:
        log_event(AUDIT_LOGGER, "tryon_invalid_request", level=logging.WARNING, reason="missing_session_id")
        return jsonify({"error": "缺少 session_id"}), 400
    if not valid_session_id(session_id):
        log_event(AUDIT_LOGGER, "tryon_invalid_request", level=logging.WARNING, reason="invalid_session_id")
        return jsonify({"error": "session_id 格式无效"}), 400
    if not session_token:
        log_event(AUDIT_LOGGER, "tryon_invalid_request", level=logging.WARNING, reason="missing_session_token")
        return jsonify({"error": "缺少 session_token"}), 400

    provided_files: Dict[str, Any] = {}
    for key, file_storage in uploaded.items():
        if file_storage is None:
            continue
        if not (file_storage.filename or "").strip():
            continue
        provided_files[key] = file_storage
    if not provided_files:
        log_event(AUDIT_LOGGER, "tryon_invalid_request", level=logging.WARNING, reason="missing_outfit_images")
        return jsonify({"error": "至少上传一张服装图片（上衣/裤子/鞋子）"}), 400

    try:
        meta = read_session_meta(session_id)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        log_event(AUDIT_LOGGER, "tryon_invalid_request", level=logging.WARNING, reason="invalid_or_expired_session")
        return jsonify({"error": "session_id 无效或已过期"}), 404

    if not verify_session_token(meta, session_token):
        log_event(AUDIT_LOGGER, "tryon_invalid_request", level=logging.WARNING, reason="invalid_session_token")
        return jsonify({"error": "session_token 无效"}), 403

    current_session_dir = SESSION_DIR / session_id
    user_photo_name = meta.get("user_photo")
    if not isinstance(user_photo_name, str) or not user_photo_name:
        log_event(AUDIT_LOGGER, "tryon_session_error", level=logging.ERROR, session_id=session_id, reason="missing_user_photo_in_meta")
        return jsonify({"error": "会话数据损坏，缺少 user_photo"}), 409
    user_photo_path = current_session_dir / user_photo_name
    if not user_photo_path.exists():
        log_event(AUDIT_LOGGER, "tryon_session_error", level=logging.ERROR, session_id=session_id, reason="missing_user_photo_file")
        return jsonify({"error": "会话原始照片不存在，请重新上传"}), 404

    saved_outfits: Dict[str, str] = {}
    try:
        for key, file_storage in provided_files.items():
            saved_outfits[key] = save_uploaded_image(file_storage, current_session_dir, OUTFIT_FIELDS[key])
    except ValueError as exc:
        log_event(
            AUDIT_LOGGER,
            "tryon_invalid_request",
            level=logging.WARNING,
            reason="invalid_outfit_image",
            error=str(exc),
            session_id=session_id,
        )
        return jsonify({"error": str(exc)}), 400

    outfit_paths = {key: current_session_dir / name for key, name in saved_outfits.items()}
    log_event(
        AUDIT_LOGGER,
        "tryon_uploaded_outfits",
        session_id=session_id,
        outfit_files=saved_outfits,
        outfit_sizes={key: path.stat().st_size for key, path in outfit_paths.items() if path.exists()},
    )
    result_name = "tryon_result.png"
    result_path = current_session_dir / result_name

    try:
        tryon_result = run_session_tryon_via_fashn(
            session_id=session_id,
            user_photo_path=user_photo_path,
            outfit_paths=outfit_paths,
            result_path=result_path,
        )
    except TryOnError as exc:
        for key, field in OUTFIT_FIELDS.items():
            if key in saved_outfits:
                meta[field] = saved_outfits[key]
            else:
                meta.pop(field, None)
        meta["tryon_provider"] = "error"
        meta["tryon_note"] = str(exc)
        meta["updated_at"] = now_iso()
        write_session_meta(session_id, meta)
        result_path.unlink(missing_ok=True)
        log_event(
            AUDIT_LOGGER,
            "tryon_failed",
            level=logging.ERROR,
            session_id=session_id,
            tryon_provider=meta.get("tryon_provider"),
            tryon_note=meta.get("tryon_note"),
        )
        return jsonify({"error": str(exc)}), 502

    for key, field in OUTFIT_FIELDS.items():
        if key in saved_outfits:
            meta[field] = saved_outfits[key]
        else:
            meta.pop(field, None)
    meta["result_photo"] = result_name
    meta["tryon_provider"] = tryon_result["provider"]
    meta["tryon_note"] = tryon_result["note"]
    meta["updated_at"] = now_iso()
    write_session_meta(session_id, meta)
    log_event(
        AUDIT_LOGGER,
        "tryon_success",
        session_id=session_id,
        tryon_provider=tryon_result["provider"],
        tryon_note=tryon_result["note"],
        result_photo=result_name,
        result_photo_size=result_path.stat().st_size if result_path.exists() else None,
    )

    return jsonify(
        {
            "session_id": session_id,
            "result_photo_url": f"/api/files/{session_id}/{result_name}?token={session_token}",
            "outfit_top_url": (
                f"/api/files/{session_id}/{meta['outfit_top']}?token={session_token}" if meta.get("outfit_top") else None
            ),
            "outfit_bottom_url": (
                f"/api/files/{session_id}/{meta['outfit_bottom']}?token={session_token}"
                if meta.get("outfit_bottom")
                else None
            ),
            "outfit_shoes_url": (
                f"/api/files/{session_id}/{meta['outfit_shoes']}?token={session_token}"
                if meta.get("outfit_shoes")
                else None
            ),
            "tryon_provider": tryon_result["provider"],
            "note": tryon_result["note"],
        }
    )


@app.errorhandler(413)
def file_too_large(_: Any) -> Any:
    log_event(AUDIT_LOGGER, "upload_too_large", level=logging.WARNING, max_upload_mb=MAX_UPLOAD_MB, path=request.path)
    return jsonify({"error": f"上传文件过大，限制 {MAX_UPLOAD_MB}MB"}), 413


@app.errorhandler(Exception)
def handle_unexpected_error(exc: Exception) -> Any:
    if isinstance(exc, HTTPException):
        return exc
    log_event(
        AUDIT_LOGGER,
        "unhandled_exception",
        level=logging.ERROR,
        path=request.path,
        method=request.method,
        error=str(exc),
    )
    app.logger.exception("Unhandled exception", exc_info=exc)
    if request.path.startswith("/api/"):
        return jsonify({"error": "服务器内部错误"}), 500
    return "Internal Server Error", 500


if __name__ == "__main__":
    port = read_int_env("PORT", 5000, min_value=1)
    app.run(host="0.0.0.0", port=port, debug=FLASK_DEBUG)
