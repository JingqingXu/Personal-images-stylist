# FASHN VTON 后端接口文档

## 1. 服务说明
- 服务类型：HTTP REST API
- 默认地址：`http://127.0.0.1:8000`
- 输出目录：`outputs/api/<task_id>/`
- 图片静态访问前缀：`/outputs/...`

启动命令：

```bash
.venv/bin/python examples/api_server.py \
  --weights-dir ./weights \
  --output-dir outputs/api \
  --host 0.0.0.0 \
  --port 8000
```

## 2. 任务模型

### 2.1 状态字段
- `status`：`queued` | `running` | `completed` | `failed`
- `stage`：`queued` | `loading_images` | `running_inference` | `saving_outputs` | `completed` | `failed`
- `progress`：任务完成度，0~100

### 2.2 队列位置字段
- `queue_position=0`：当前正在执行
- `queue_position=1,2,...`：等待队列中的位置（包含正在执行的任务作为第 0 位）
- `queue_position=null`：任务已结束（`completed/failed`）

## 3. 接口列表
- `GET /health`：健康检查
- `GET /queue`：查看当前队列
- `POST /tryon`：提交换装任务（异步）
- `GET /tasks/{task_id}`：查询任务状态/进度/结果

---

## 4. 接口详情

### 4.1 健康检查
`GET /health`

响应示例：

```json
{
  "status": "ok",
  "weights_dir": "weights",
  "output_dir": "outputs/api",
  "device": "cuda",
  "active_task_id": "2c39e7c734e6",
  "pending_tasks": 1
}
```

字段说明：
- `active_task_id`：当前运行中的任务 ID，无任务时为 `null`
- `pending_tasks`：等待队列任务数（不含运行中）

---

### 4.2 查询队列
`GET /queue`

响应示例：

```json
{
  "active_task_id": "2c39e7c734e6",
  "pending_count": 1,
  "pending_task_ids": ["92369f91c2f5"]
}
```

---

### 4.3 提交换装任务
`POST /tryon`

请求类型：`multipart/form-data`

参数：
- `person_image`：文件，必填，人物图
- `garment_image`：文件，必填，服装图
- `category`：字符串，必填，`tops | bottoms | one-pieces`
- `garment_photo_type`：字符串，可选，默认 `model`，可选 `model | flat-lay`
- `num_samples`：整数，可选，默认 `1`，范围 `1~4`
- `num_timesteps`：整数，可选，默认 `30`，最小 `1`
- `guidance_scale`：浮点数，可选，默认 `1.5`
- `seed`：整数，可选，默认 `42`
- `segmentation_free`：布尔，可选，默认 `true`

请求示例：

```bash
curl -X POST "http://127.0.0.1:8000/tryon" \
  -F "person_image=@examples/data/models.jpeg" \
  -F "garment_image=@examples/data/garment.jpg" \
  -F "category=tops"
```

响应示例：

```json
{
  "message": "task accepted",
  "task_id": "2c39e7c734e6",
  "status": "queued",
  "progress": 0,
  "queue_position": 0,
  "task_status_url": "http://127.0.0.1:8000/tasks/2c39e7c734e6"
}
```

说明：
- 此接口为异步提交，不会等待图片生成完成。
- 请通过 `task_status_url` 或 `GET /tasks/{task_id}` 轮询任务进度。

---

### 4.4 查询任务状态
`GET /tasks/{task_id}`

响应示例（运行中）：

```json
{
  "task_id": "2c39e7c734e6",
  "status": "running",
  "stage": "running_inference",
  "progress": 35,
  "queue_position": 0,
  "created_at": 1778673721.6575785,
  "started_at": 1778673721.6577659,
  "finished_at": null,
  "elapsed_seconds": null,
  "category": "tops",
  "garment_photo_type": "model",
  "inputs": {
    "person_image": "outputs/api/2c39e7c734e6/person.jpeg",
    "garment_image": "outputs/api/2c39e7c734e6/garment.jpg"
  },
  "outputs": [],
  "error": null
}
```

响应示例（完成）：

```json
{
  "task_id": "2c39e7c734e6",
  "status": "completed",
  "stage": "completed",
  "progress": 100,
  "queue_position": null,
  "created_at": 1778673721.6575785,
  "started_at": 1778673721.6577659,
  "finished_at": 1778673739.9297915,
  "elapsed_seconds": 18.118,
  "category": "tops",
  "garment_photo_type": "model",
  "inputs": {
    "person_image": "outputs/api/2c39e7c734e6/person.jpeg",
    "garment_image": "outputs/api/2c39e7c734e6/garment.jpg"
  },
  "outputs": [
    {
      "index": 0,
      "file_path": "outputs/api/2c39e7c734e6/output_00.png",
      "url": "http://127.0.0.1:8000/outputs/2c39e7c734e6/output_00.png",
      "url_path": "/outputs/2c39e7c734e6/output_00.png"
    }
  ],
  "error": null
}
```

失败时：
- `status=failed`
- `stage=failed`
- `progress=100`
- `error` 包含失败原因

---

## 5. 错误码
- `400`：参数错误、图片为空、图片格式非法
- `404`：任务不存在（`task_id` 无效）
- `500`：服务内部错误（通常出现在任务执行阶段，错误信息在任务 `error` 字段）

## 6. 客户端接入建议
- 提交任务后保存 `task_id`
- 每 `2~5` 秒轮询一次 `GET /tasks/{task_id}`
- 当 `status=completed` 时读取 `outputs[].url` 展示结果
- 当 `status=failed` 时展示 `error` 文本并支持重试
