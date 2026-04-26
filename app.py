import base64
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error as url_error
from urllib import request as url_request

from flask import Flask, jsonify, render_template, request, send_from_directory
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
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))

QWEN_ENDPOINT = os.getenv(
    "QWEN_ENDPOINT",
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
)
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-vl-max-latest")

# 固定请求模板：分析用户人像并输出可执行的形象建议
QWEN_STYLE_PROMPT_TEMPLATE = """
你是一名高级个人形象顾问。请根据用户提供的人像照片，输出简洁、可执行的中文建议。
请按以下结构回复：
1) 形象定位（1-2句）
2) 推荐色彩（3-5个关键词）
3) 三套穿搭建议（每套包含上装/下装/鞋/配饰）
4) 发型与妆容要点（3条）
5) 避雷建议（3条）
6) 为了更准确换装，用户下一步应上传的服装照片要求（角度、光线、背景）
要求：
- 语言简洁直接
- 不要输出免责声明
- 不要使用 JSON
""".strip()

TRYON_PROMPT_TEMPLATE = (
    "请基于用户人像与服装图片进行自然换装，保留用户面部与体型特征，"
    "让服装贴合真实比例与褶皱，输出写实结果图。"
)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["JSON_AS_ASCII"] = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 120) -> Dict[str, Any]:
    req = url_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with url_request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


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


def fallback_advice() -> str:
    return (
        "形象定位：简洁利落的通勤休闲风，重点提升上半身层次与色彩协调。\n"
        "推荐色彩：雾霾蓝、米白、炭灰、卡其、深牛仔蓝。\n"
        "穿搭建议1：短款夹克 + 纯色T恤 + 直筒牛仔裤 + 小白鞋。\n"
        "穿搭建议2：轻薄针织开衫 + 内搭衬衫 + 九分西裤 + 乐福鞋。\n"
        "穿搭建议3：挺阔风衣 + 修身打底 + 高腰阔腿裤 + 短靴。\n"
        "发型与妆容：顶部增加蓬松度；眉眼线条更清晰；唇色选低饱和豆沙系。\n"
        "避雷建议：避免过多高饱和撞色；避免过松无版型上衣；避免鞋裤颜色完全断层。\n"
        "服装照片上传要求：单件服装平铺或正挂，正面拍摄，光线均匀，背景干净，避免遮挡与折叠。"
    )


def call_qwen_style_advice(user_photo_path: Path) -> Dict[str, str]:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        return {
            "provider": "fallback",
            "advice": fallback_advice(),
            "note": "未配置 DASHSCOPE_API_KEY，已返回本地默认建议。",
        }

    payload = {
        "model": QWEN_MODEL,
        "temperature": 0.2,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": QWEN_STYLE_PROMPT_TEMPLATE},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url_from_image(user_photo_path)},
                    },
                ],
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = post_json(QWEN_ENDPOINT, payload, headers, timeout=120)
        advice = extract_chat_text(response)
        if not advice:
            raise ValueError("Qwen response missing message content")
        return {"provider": "qwen", "advice": advice, "note": ""}
    except (url_error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {
            "provider": "fallback",
            "advice": fallback_advice(),
            "note": f"调用千问失败，已回退默认建议：{exc}",
        }


def deep_get(data: Dict[str, Any], keys: list[str]) -> Optional[Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def decode_b64_to_file(value: str, output_path: Path) -> None:
    raw = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    output_path.write_bytes(base64.b64decode(raw))


def download_file(url: str, output_path: Path) -> None:
    req = url_request.Request(url, method="GET")
    with url_request.urlopen(req, timeout=120) as resp:
        output_path.write_bytes(resp.read())


def try_call_tryon_model(person_path: Path, outfit_path: Path, advice: str) -> Dict[str, Any]:
    api_url = os.getenv("TRYON_API_URL", "").strip()
    if not api_url:
        raise RuntimeError("TRYON_API_URL is not configured")

    api_key = os.getenv("TRYON_API_KEY", "").strip()
    model_name = os.getenv("TRYON_MODEL", "virtual-try-on")

    payload = {
        "model": model_name,
        "input": {
            "person_image": data_url_from_image(person_path),
            "garment_image": data_url_from_image(outfit_path),
            "prompt": TRYON_PROMPT_TEMPLATE,
            "style_advice": advice,
        },
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return post_json(api_url, payload, headers, timeout=180)


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
            return True

    for path in url_candidates:
        value = deep_get(response_data, path)
        if isinstance(value, str) and value.strip():
            download_file(value.strip(), output_path)
            return True

    return False


def build_tryon_result(
    user_photo_path: Path,
    outfit_photo_path: Path,
    advice: str,
    output_path: Path,
) -> Dict[str, str]:
    try:
        response_data = try_call_tryon_model(user_photo_path, outfit_photo_path, advice)
        if save_tryon_result(response_data, output_path):
            return {"provider": "tryon_api", "note": ""}
        raise ValueError("换装模型返回中未找到可用图片字段")
    except Exception as exc:
        # 降级逻辑：未配置或调用失败时，先返回用户原图，保证流程可跑通
        shutil.copyfile(user_photo_path, output_path)
        return {
            "provider": "mock",
            "note": f"换装模型调用失败，已返回原图占位结果：{exc}",
        }


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
    return target_name


def session_meta_path(session_id: str) -> Path:
    return SESSION_DIR / session_id / "meta.json"


def read_session_meta(session_id: str) -> Dict[str, Any]:
    path = session_meta_path(session_id)
    if not path.exists():
        raise FileNotFoundError("会话不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def write_session_meta(session_id: str, meta: Dict[str, Any]) -> None:
    path = session_meta_path(session_id)
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@app.route("/", methods=["GET"])
def index() -> Any:
    return render_template("index.html")


@app.route("/api/health", methods=["GET"])
def health() -> Any:
    return jsonify({"status": "ok", "time": now_iso()})


@app.route("/api/files/<session_id>/<filename>", methods=["GET"])
def session_file(session_id: str, filename: str) -> Any:
    directory = SESSION_DIR / session_id
    return send_from_directory(directory, filename)


@app.route("/api/analyze", methods=["POST"])
def analyze() -> Any:
    user_photo = request.files.get("user_photo")
    if user_photo is None:
        return jsonify({"error": "缺少 user_photo 文件"}), 400

    session_id = uuid.uuid4().hex
    current_session_dir = SESSION_DIR / session_id
    current_session_dir.mkdir(parents=True, exist_ok=True)

    try:
        user_photo_name = save_uploaded_image(user_photo, current_session_dir, "user_photo")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    user_photo_path = current_session_dir / user_photo_name

    advice_result = call_qwen_style_advice(user_photo_path)

    meta = {
        "session_id": session_id,
        "created_at": now_iso(),
        "user_photo": user_photo_name,
        "advice": advice_result["advice"],
        "advice_provider": advice_result["provider"],
        "advice_note": advice_result["note"],
    }
    write_session_meta(session_id, meta)

    return jsonify(
        {
            "session_id": session_id,
            "advice": advice_result["advice"],
            "advice_provider": advice_result["provider"],
            "note": advice_result["note"],
            "user_photo_url": f"/api/files/{session_id}/{user_photo_name}",
        }
    )


@app.route("/api/try-on", methods=["POST"])
def try_on() -> Any:
    session_id = (request.form.get("session_id") or "").strip()
    outfit_photo = request.files.get("outfit_photo")

    if not session_id:
        return jsonify({"error": "缺少 session_id"}), 400

    if outfit_photo is None:
        return jsonify({"error": "缺少 outfit_photo 文件"}), 400

    try:
        meta = read_session_meta(session_id)
    except FileNotFoundError:
        return jsonify({"error": "session_id 无效或已过期"}), 404

    current_session_dir = SESSION_DIR / session_id

    try:
        outfit_name = save_uploaded_image(outfit_photo, current_session_dir, "outfit_photo")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    user_photo_path = current_session_dir / meta["user_photo"]
    outfit_photo_path = current_session_dir / outfit_name
    result_name = "tryon_result.png"
    result_path = current_session_dir / result_name

    tryon_result = build_tryon_result(
        user_photo_path=user_photo_path,
        outfit_photo_path=outfit_photo_path,
        advice=meta.get("advice", ""),
        output_path=result_path,
    )

    meta["outfit_photo"] = outfit_name
    meta["result_photo"] = result_name
    meta["tryon_provider"] = tryon_result["provider"]
    meta["tryon_note"] = tryon_result["note"]
    meta["updated_at"] = now_iso()
    write_session_meta(session_id, meta)

    return jsonify(
        {
            "session_id": session_id,
            "result_photo_url": f"/api/files/{session_id}/{result_name}",
            "outfit_photo_url": f"/api/files/{session_id}/{outfit_name}",
            "tryon_provider": tryon_result["provider"],
            "note": tryon_result["note"],
        }
    )


@app.errorhandler(413)
def file_too_large(_: Any) -> Any:
    return jsonify({"error": f"上传文件过大，限制 {MAX_UPLOAD_MB}MB"}), 413


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
