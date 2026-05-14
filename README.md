# 个人形象设计与智能换装系统

这个项目实现了你描述的完整流程：

1. 前端上传个人照片。
2. 后端把个人照片连同固定提示词发送给配置中的穿搭建议模型，返回形象建议。
3. 前端展示建议后，用户上传上衣、裤子、鞋子中的 1-3 张服装照片。
4. 后端把个人照+已上传的服装图（1-3张）发送给换装模型，拿到结果图回传前端展示。

## 目录结构

```text
personal-image-stylist/
├── app.py
├── requirements.txt
├── .env.example
├── config/
│   ├── model_providers.yaml
│   └── system_prompts.yaml
├── templates/
│   └── index.html
└── static/
    ├── app.js
    └── styles.css
```

## 快速启动

```bash
cd /home/user/personal-image-stylist
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 按需修改 .env
python app.py
```

浏览器访问：`http://127.0.0.1:5000`

## 环境变量说明

- `PORT`：Flask 运行端口，默认 `5000`。
- `FLASK_DEBUG`：是否开启调试模式，默认 `0`（生产环境务必保持关闭）。
- `MAX_UPLOAD_MB`：单文件上传体积限制（MB），默认 `10`。
- `SESSION_TTL_HOURS`：会话保留时长（小时），默认 `24`；设为 `0` 表示不自动过期。
- `SESSION_CLEANUP_INTERVAL_SECONDS`：后台清理任务最小触发间隔（秒），默认 `600`。
- `MODEL_CONFIG_PATH`：模型配置文件路径（YAML）；不填默认读取 `./config/model_providers.yaml`。
- `SYSTEM_PROMPT_CONFIG_PATH`：系统提示词配置文件路径（YAML）；不填默认读取 `./config/system_prompts.yaml`。
- `STYLE_ADVICE_API_KEY`：穿搭建议模型的独立鉴权变量（推荐）。
- `TRYON_API_KEY`：换装模型的独立鉴权变量（推荐）。
- `DASHSCOPE_API_KEY` / `ZHIPU_API_KEY`：兼容旧配置时可继续使用。实际读取哪个变量由 `model_providers.yaml` 中各 provider 的 `api_key_env` 或 `api_key_envs` 决定。
- `APP_LOG_LEVEL`：日志级别，默认 `INFO`。
- `APP_LOG_PATH`：接口审计日志文件路径，默认 `./data/logs/app.log`。
- `MODEL_LOG_PATH`：模型调用日志文件路径，默认 `./data/logs/model_calls.log`。
- `APP_LOG_MAX_BYTES`：单个日志文件滚动大小（字节），默认 `10485760`。
- `APP_LOG_BACKUP_COUNT`：日志滚动保留数量，默认 `5`。
- `LOG_MAX_FIELD_CHARS`：单字段最大日志长度，默认 `4000`（超长会截断）。
- `LOG_INCLUDE_IMAGE_BASE64`：是否在日志中保留图片 Base64，默认 `0`（仅记录摘要 hash 与长度）。

## 日志说明

- `app.log`：记录 API 请求开始/结束、参数校验失败、业务成功/失败、未处理异常。
- `model_calls.log`：记录模型调用链路（provider/model/endpoint/请求 payload/响应/错误/重试）。
- 每条日志为单行 JSON，并带有 `request_id`，可按一次请求追踪全链路。

## 系统提示词配置（YAML）

系统会在每次请求时读取 `config/system_prompts.yaml`（可通过 `SYSTEM_PROMPT_CONFIG_PATH` 覆盖）。

你只改这个 YAML 文件，就可以修改：
- `prompts.style_advice`（穿搭建议提示词）
- `prompts.tryon`（换装提示词）

示例：

```yaml
prompts:
  style_advice: |
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
  tryon: >-
    请基于用户人像与用户上传的服装图片进行自然换装。用户可能只上传上衣、裤子、鞋子中的部分图片，也可能全部上传。保持用户面部与体型特征，让服装贴合真实比例与褶皱，输出写实结果图。
```

## 模型配置（YAML）

系统会在每次请求时读取 `config/model_providers.yaml`（可通过 `MODEL_CONFIG_PATH` 覆盖）。

你只改这一个 YAML 文件，就可以：
- 切换穿搭建议模型厂商（`style_advice.active_provider`）
- 切换换装接口模式（`tryon.active_backend`：`vendor_api` 或 `local_model`）
- 切换换装厂商（`tryon.vendor_api.active_vendor`）
- 给穿搭建议和换装分别配置不同 API（各自 endpoint、model、鉴权变量互不影响）

示例：

```yaml
style_advice:
  active_provider: dashscope
  providers:
    dashscope:
      type: openai_compatible
      endpoint: https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
      model: qwen-vl-max-latest
      api_key_envs:
        - STYLE_ADVICE_API_KEY
        - DASHSCOPE_API_KEY
      temperature: 0.2
      require_api_key: true
      auth_mode: bearer
    mock:
      type: mock

tryon:
  active_backend: vendor_api
  vendor_api:
    active_vendor: default_vendor
    vendors:
      default_vendor:
        type: openai_compatible
        endpoint: http://your-vendor-api/tryon
        model: virtual-try-on
        api_key_env: TRYON_API_KEY
        timeout_seconds: 180
        require_api_key: true
        auth_mode: bearer
  local_model:
    type: http
    endpoint: http://127.0.0.1:8001/tryon
    model: local-tryon
    timeout_seconds: 300
    require_api_key: false
```

说明：
- `style_advice.providers.*`：穿搭建议厂商配置（`type=openai_compatible` 或 `mock`），可使用 `api_key_env` 或 `api_key_envs`。
- `tryon.active_backend`：换装调用路径，`vendor_api`=调用厂商 API，`local_model`=调用本地模型服务。
- `tryon.vendor_api.active_vendor`：当前启用的换装厂商。
- `auth_mode`：`bearer` 或 `header`（`header` 时可配 `auth_header`）。

## API 说明

### `POST /api/analyze`

- `form-data`: `user_photo`（图片）
- 返回：`session_id`, `session_token`, `advice`, `user_photo_url`

### `POST /api/try-on`

- `form-data`: `session_id`, `session_token`, `outfit_top`, `outfit_bottom`, `outfit_shoes`（图片，三者可选但至少上传一张）
- 返回：`result_photo_url`, `outfit_top_url`, `outfit_bottom_url`, `outfit_shoes_url`
- 若换装模型不可用或返回异常，会返回 5xx 错误，不再返回“占位原图”。

### `GET /api/files/<session_id>/<filename>`

- 必须携带 `?token=<session_token>` 才可读取。
- 仅允许读取会话公开图片（个人照、上衣、裤子、鞋子、换装结果），不能读取 `meta.json` 等内部文件。

## 说明

- 当前换装接口采用“可配置适配”方式，默认兼容常见 JSON 返回格式（`image_base64` 或 `image_url`）。
- 若你的换装模型入参/出参字段不同，只需改 `app.py` 中：
  - `try_call_tryon_model()`（请求体字段）
  - `save_tryon_result()`（响应解析字段）
- 后端会校验上传图片是否为真实可解析图像，非法内容会被拒绝。
