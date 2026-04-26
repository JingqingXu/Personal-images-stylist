# 个人形象设计与智能换装系统

这个项目实现了你描述的完整流程：

1. 前端上传个人照片。
2. 后端把个人照片连同固定提示词发送给千问模型，返回形象建议。
3. 前端展示建议后，用户上传服装照片。
4. 后端把个人照+服装照发送给换装模型，拿到结果图回传前端展示。

## 目录结构

```text
personal-image-stylist/
├── app.py
├── requirements.txt
├── .env.example
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

- `DASHSCOPE_API_KEY`：千问 API Key（不填则自动使用本地默认建议）。
- `QWEN_ENDPOINT`：千问接口地址，默认 DashScope OpenAI 兼容接口。
- `QWEN_MODEL`：千问视觉模型名，默认 `qwen-vl-max-latest`。
- `TRYON_API_URL`：换装模型接口地址（不填则走 mock，占位返回原图）。
- `TRYON_API_KEY`：换装模型鉴权。
- `TRYON_MODEL`：换装模型名。

## 固定请求模板位置

`app.py` 里的常量：

- `QWEN_STYLE_PROMPT_TEMPLATE`（千问建议固定模板）
- `TRYON_PROMPT_TEMPLATE`（换装任务提示）

你可以只改这两个模板文案，不影响前后端流程。

## API 说明

### `POST /api/analyze`

- `form-data`: `user_photo`（图片）
- 返回：`session_id`, `advice`, `user_photo_url`

### `POST /api/try-on`

- `form-data`: `session_id`, `outfit_photo`（图片）
- 返回：`result_photo_url`

### `GET /api/files/<session_id>/<filename>`

- 读取会话内图片（个人照、服装照、换装结果）。

## 说明

- 当前换装接口采用“可配置适配”方式，默认兼容常见 JSON 返回格式（`image_base64` 或 `image_url`）。
- 若你的换装模型入参/出参字段不同，只需改 `app.py` 中：
  - `try_call_tryon_model()`（请求体字段）
  - `save_tryon_result()`（响应解析字段）

