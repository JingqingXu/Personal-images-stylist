import io
import json
import os
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import app as app_module
from PIL import Image


def make_png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color=(120, 120, 120)).save(output, format="PNG")
    return output.getvalue()


PNG_BYTES = make_png_bytes()


class ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_session_dir = app_module.SESSION_DIR
        self.original_vton_output_dir = app_module.VTON_OUTPUT_DIR
        self.original_vton_output_label = app_module.VTON_OUTPUT_DIR_LABEL
        self.original_ttl = app_module.SESSION_TTL_HOURS
        self.original_cleanup_interval = app_module.SESSION_CLEANUP_INTERVAL_SECONDS
        self.original_cleanup_tick = app_module.LAST_CLEANUP_MONOTONIC
        self.original_model_config_cache = dict(app_module.MODEL_CONFIG_CACHE)
        self.original_system_prompt_config_cache = dict(app_module.SYSTEM_PROMPT_CONFIG_CACHE)

        app_module.SESSION_DIR = Path(self.tmpdir.name) / "sessions"
        app_module.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        app_module.VTON_OUTPUT_DIR_LABEL = "outputs/api"
        app_module.VTON_OUTPUT_DIR = Path(self.tmpdir.name) / app_module.VTON_OUTPUT_DIR_LABEL
        app_module.VTON_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        app_module.SESSION_TTL_HOURS = 24
        app_module.SESSION_CLEANUP_INTERVAL_SECONDS = 600
        app_module.LAST_CLEANUP_MONOTONIC = 0.0
        app_module.MODEL_CONFIG_CACHE = {"path": None, "mtime": None, "data": None}
        app_module.SYSTEM_PROMPT_CONFIG_CACHE = {"path": None, "mtime": None, "data": None}
        with app_module.TRYON_TASK_CONDITION:
            app_module.TRYON_TASKS.clear()
            app_module.TRYON_PENDING_TASK_IDS.clear()
            app_module.TRYON_ACTIVE_TASK_ID = None

        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        app_module.SESSION_DIR = self.original_session_dir
        app_module.VTON_OUTPUT_DIR = self.original_vton_output_dir
        app_module.VTON_OUTPUT_DIR_LABEL = self.original_vton_output_label
        app_module.SESSION_TTL_HOURS = self.original_ttl
        app_module.SESSION_CLEANUP_INTERVAL_SECONDS = self.original_cleanup_interval
        app_module.LAST_CLEANUP_MONOTONIC = self.original_cleanup_tick
        app_module.MODEL_CONFIG_CACHE = self.original_model_config_cache
        app_module.SYSTEM_PROMPT_CONFIG_CACHE = self.original_system_prompt_config_cache
        self.tmpdir.cleanup()

    def analyze_ok(self) -> dict:
        resp = self.client.post(
            "/api/analyze",
            data={"user_photo": (io.BytesIO(PNG_BYTES), "user.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()

    def tryon_files(self) -> dict:
        return {
            "outfit_top": (io.BytesIO(PNG_BYTES), "top.png"),
            "outfit_bottom": (io.BytesIO(PNG_BYTES), "bottom.png"),
            "outfit_shoes": (io.BytesIO(PNG_BYTES), "shoes.png"),
        }

    def test_rejects_invalid_image_content(self) -> None:
        resp = self.client.post(
            "/api/analyze",
            data={"user_photo": (io.BytesIO(b"not-an-image"), "fake.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("有效图片", resp.get_json()["error"])

    def test_files_require_token_and_block_meta(self) -> None:
        data = self.analyze_ok()
        session_id = data["session_id"]
        session_token = data["session_token"]

        file_path = f"/api/files/{session_id}/user_photo.png"
        missing_token = self.client.get(file_path)
        self.assertEqual(missing_token.status_code, 403)

        blocked_meta = self.client.get(f"/api/files/{session_id}/meta.json?token={session_token}")
        self.assertEqual(blocked_meta.status_code, 404)

        ok_file = self.client.get(file_path + f"?token={session_token}")
        self.assertEqual(ok_file.status_code, 200)
        self.assertGreater(len(ok_file.data), 0)
        ok_file.close()

    def test_tryon_requires_valid_session_token(self) -> None:
        data = self.analyze_ok()
        resp = self.client.post(
            "/api/try-on",
            data={
                "session_id": data["session_id"],
                "session_token": "bad-token",
                **self.tryon_files(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 403)

    def test_tryon_requires_at_least_one_outfit_part(self) -> None:
        data = self.analyze_ok()
        resp = self.client.post(
            "/api/try-on",
            data={
                "session_id": data["session_id"],
                "session_token": data["session_token"],
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("至少上传一张", resp.get_json()["error"])

    def test_tryon_allows_partial_outfit_parts(self) -> None:
        data = self.analyze_ok()
        def fake_run_session_tryon_via_fashn(
            session_id: str,
            user_photo_path: Path,
            outfit_paths: dict,
            result_path: Path,
        ) -> dict:
            self.assertEqual(session_id, data["session_id"])
            self.assertIn("top", outfit_paths)
            result_path.write_bytes(PNG_BYTES)
            return {"provider": "fashn_vton", "note": "mock"}

        with mock.patch.object(app_module, "run_session_tryon_via_fashn", side_effect=fake_run_session_tryon_via_fashn):
            resp = self.client.post(
                "/api/try-on",
                data={
                    "session_id": data["session_id"],
                    "session_token": data["session_token"],
                    "outfit_top": (io.BytesIO(PNG_BYTES), "top.png"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["tryon_provider"], "fashn_vton")

    def test_tryon_returns_502_when_not_configured(self) -> None:
        data = self.analyze_ok()
        with mock.patch.object(
            app_module,
            "run_session_tryon_via_fashn",
            side_effect=app_module.TryOnError("提交到 FASHN-VTON 服务失败，请先启动 /mnt/16t/fashn-vton-1.5/examples/api_server.py"),
        ):
            resp = self.client.post(
                "/api/try-on",
                data={
                    "session_id": data["session_id"],
                    "session_token": data["session_token"],
                    **self.tryon_files(),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 502)
        self.assertIn("FASHN-VTON", resp.get_json()["error"])

    def test_style_provider_can_switch_by_config_file(self) -> None:
        cfg_path = Path(self.tmpdir.name) / "model-providers.yaml"
        cfg_path.write_text(
            """
style_advice:
  active_provider: mock_vendor
  providers:
    mock_vendor:
      type: mock

tryon:
  active_backend: local_model
  vendor_api:
    active_vendor: any
    vendors:
      any:
        type: openai_compatible
        endpoint: http://invalid.local
        model: invalid
  local_model:
    type: http
    endpoint: http://127.0.0.1:8001/tryon
    model: local-tryon
""".strip(),
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"MODEL_CONFIG_PATH": str(cfg_path)}, clear=False):
            resp = self.client.post(
                "/api/analyze",
                data={"user_photo": (io.BytesIO(PNG_BYTES), "user.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["advice_provider"], "mock_vendor")

    def test_style_prompt_can_be_overridden_by_system_config(self) -> None:
        model_cfg_path = Path(self.tmpdir.name) / "model-providers-style.yaml"
        model_cfg_path.write_text(
            """
style_advice:
  active_provider: style_vendor
  providers:
    style_vendor:
      type: openai_compatible
      endpoint: http://style.invalid/chat
      model: style-model
      require_api_key: false

tryon:
  active_backend: local_model
  vendor_api:
    active_vendor: any
    vendors:
      any:
        type: openai_compatible
        endpoint: http://vendor.invalid/tryon
        model: vendor-model
        require_api_key: false
  local_model:
    type: http
    endpoint: http://127.0.0.1:8001/tryon
    model: local-tryon
""".strip(),
            encoding="utf-8",
        )
        prompt_cfg_path = Path(self.tmpdir.name) / "system-prompts-style.yaml"
        prompt_cfg_path.write_text(
            """
prompts:
  style_advice: |
    这是一个自定义的穿搭建议提示词
  tryon: >-
    这是默认换装提示词
""".strip(),
            encoding="utf-8",
        )

        mock_response = {"choices": [{"message": {"content": "自定义建议"}}]}
        with mock.patch.dict(
            os.environ,
            {
                "MODEL_CONFIG_PATH": str(model_cfg_path),
                "SYSTEM_PROMPT_CONFIG_PATH": str(prompt_cfg_path),
            },
            clear=False,
        ):
            with mock.patch.object(app_module, "post_json", return_value=mock_response) as mocked_post_json:
                resp = self.client.post(
                    "/api/analyze",
                    data={"user_photo": (io.BytesIO(PNG_BYTES), "user.png")},
                    content_type="multipart/form-data",
                )

        self.assertEqual(resp.status_code, 200)
        mocked_post_json.assert_called()
        payload = mocked_post_json.call_args.args[1]
        self.assertEqual(payload["messages"][0]["content"][0]["text"], "这是一个自定义的穿搭建议提示词")

    def test_tryon_prompt_can_be_overridden_by_system_config(self) -> None:
        analyze_data = self.analyze_ok()

        def fake_run_session_tryon_via_fashn(
            session_id: str,
            user_photo_path: Path,
            outfit_paths: dict,
            result_path: Path,
        ) -> dict:
            self.assertEqual(session_id, analyze_data["session_id"])
            self.assertIn("top", outfit_paths)
            result_path.write_bytes(PNG_BYTES)
            return {"provider": "fashn_vton", "note": "source=mock"}

        with mock.patch.object(app_module, "run_session_tryon_via_fashn", side_effect=fake_run_session_tryon_via_fashn):
            resp = self.client.post(
                "/api/try-on",
                data={
                    "session_id": analyze_data["session_id"],
                    "session_token": analyze_data["session_token"],
                    "outfit_top": (io.BytesIO(PNG_BYTES), "top.png"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["tryon_provider"], "fashn_vton")

    def test_tryon_can_switch_to_local_model_backend(self) -> None:
        analyze_data = self.analyze_ok()

        def fake_run_session_tryon_via_fashn(
            session_id: str,
            user_photo_path: Path,
            outfit_paths: dict,
            result_path: Path,
        ) -> dict:
            result_path.write_bytes(PNG_BYTES)
            return {"provider": "fashn_vton", "note": "mock"}

        with mock.patch.object(
            app_module,
            "run_session_tryon_via_fashn",
            side_effect=fake_run_session_tryon_via_fashn,
        ):
            resp = self.client.post(
                "/api/try-on",
                data={
                    "session_id": analyze_data["session_id"],
                    "session_token": analyze_data["session_token"],
                    "outfit_top": (io.BytesIO(PNG_BYTES), "top.png"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["tryon_provider"], "fashn_vton")

    def test_tryon_can_switch_vendor_by_config(self) -> None:
        analyze_data = self.analyze_ok()
        captured = {"keys": set()}

        def fake_run_session_tryon_via_fashn(
            session_id: str,
            user_photo_path: Path,
            outfit_paths: dict,
            result_path: Path,
        ) -> dict:
            captured["keys"] = set(outfit_paths.keys())
            result_path.write_bytes(PNG_BYTES)
            return {"provider": "fashn_vton", "note": "mock"}

        with mock.patch.object(app_module, "run_session_tryon_via_fashn", side_effect=fake_run_session_tryon_via_fashn):
            resp = self.client.post(
                "/api/try-on",
                data={
                    "session_id": analyze_data["session_id"],
                    "session_token": analyze_data["session_token"],
                    "outfit_bottom": (io.BytesIO(PNG_BYTES), "bottom.png"),
                    "outfit_shoes": (io.BytesIO(PNG_BYTES), "shoes.png"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["keys"], {"bottom", "shoes"})

    def test_cleanup_expired_sessions(self) -> None:
        old_session_id = uuid.uuid4().hex
        old_session_dir = app_module.SESSION_DIR / old_session_id
        old_session_dir.mkdir(parents=True, exist_ok=True)

        old_time = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
        meta_path = old_session_dir / "meta.json"
        meta_path.write_text(
            json.dumps(
                {
                    "session_id": old_session_id,
                    "session_token": "token",
                    "created_at": old_time,
                    "user_photo": "user_photo.png",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        app_module.SESSION_TTL_HOURS = 1
        app_module.cleanup_expired_sessions()
        self.assertFalse(old_session_dir.exists())

    def test_async_tryon_task_flow(self) -> None:
        def fake_submit_fashn_tryon_task(task: dict, person_path: Path, garment_path: Path) -> str:
            self.assertTrue(person_path.exists())
            self.assertTrue(garment_path.exists())
            return "upstream_task_123"

        def fake_poll_fashn_task_until_done(upstream_task_id: str, task_id: str) -> dict:
            self.assertEqual(upstream_task_id, "upstream_task_123")
            return {"status": "completed", "outputs": [{"index": 0}, {"index": 1}]}

        def fake_sync_fashn_outputs_to_local(task_id: str, task_dir: Path, upstream_task_response: dict) -> list[dict]:
            self.assertEqual(upstream_task_response["status"], "completed")
            for idx in range(2):
                (task_dir / f"output_{idx:02d}.png").write_bytes(PNG_BYTES)
            return [
                {
                    "index": 0,
                    "file_path": f"{app_module.VTON_OUTPUT_DIR_LABEL}/{task_id}/output_00.png",
                    "url_path": f"/outputs/{task_id}/output_00.png",
                },
                {
                    "index": 1,
                    "file_path": f"{app_module.VTON_OUTPUT_DIR_LABEL}/{task_id}/output_01.png",
                    "url_path": f"/outputs/{task_id}/output_01.png",
                },
            ]

        with (
            mock.patch.object(app_module, "submit_fashn_tryon_task", side_effect=fake_submit_fashn_tryon_task),
            mock.patch.object(app_module, "poll_fashn_task_until_done", side_effect=fake_poll_fashn_task_until_done),
            mock.patch.object(app_module, "sync_fashn_outputs_to_local", side_effect=fake_sync_fashn_outputs_to_local),
        ):
            resp = self.client.post(
                "/tryon",
                data={
                    "person_image": (io.BytesIO(PNG_BYTES), "person.png"),
                    "garment_image": (io.BytesIO(PNG_BYTES), "garment.png"),
                    "category": "tops",
                    "num_samples": "2",
                },
                content_type="multipart/form-data",
            )

            self.assertEqual(resp.status_code, 200)
            payload = resp.get_json()
            self.assertEqual(payload["status"], "queued")
            self.assertIn("task_id", payload)
            task_id = payload["task_id"]

            status_payload = None
            for _ in range(30):
                status_resp = self.client.get(f"/tasks/{task_id}")
                self.assertEqual(status_resp.status_code, 200)
                status_payload = status_resp.get_json()
                if status_payload["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)

        self.assertIsNotNone(status_payload)
        self.assertEqual(status_payload["status"], "completed")
        self.assertEqual(status_payload["progress"], 100)
        self.assertEqual(len(status_payload["outputs"]), 2)
        self.assertIsNone(status_payload["error"])
        output_item = status_payload["outputs"][0]
        self.assertEqual(output_item["url_path"], f"/outputs/{task_id}/output_00.png")
        file_resp = self.client.get(output_item["url_path"])
        self.assertEqual(file_resp.status_code, 200)
        self.assertGreater(len(file_resp.data), 0)
        file_resp.close()

    def test_vton_health_and_queue(self) -> None:
        health_resp = self.client.get("/health")
        self.assertEqual(health_resp.status_code, 200)
        health_json = health_resp.get_json()
        self.assertEqual(health_json["status"], "ok")
        self.assertIn("output_dir", health_json)

        queue_resp = self.client.get("/queue")
        self.assertEqual(queue_resp.status_code, 200)
        queue_json = queue_resp.get_json()
        self.assertIn("active_task_id", queue_json)
        self.assertIn("pending_count", queue_json)
        self.assertIn("pending_task_ids", queue_json)


if __name__ == "__main__":
    unittest.main()
