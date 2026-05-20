import json
from pathlib import Path
from typing import Any

import numpy as np
from skimage.metrics import structural_similarity as ssim


class ReplayAssertion:
    """读取录制数据，与新运行的结果做对比断言"""

    def __init__(self, record_dir: Path):
        self._record_dir = Path(record_dir)
        self._actions = []
        self._load()

    def _load(self):
        path = self._record_dir / "actions.jsonl"
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self._actions.append(json.loads(line))

    @property
    def actions(self) -> list[dict]:
        return self._actions

    def assert_click_position_close(self,
                                     action_idx: int,
                                     actual_x: int,
                                     actual_y: int,
                                     tolerance: int = 5) -> None:
        """验证点击坐标与录制数据的偏差在容忍范围内"""
        expected = self._actions[action_idx]
        assert expected["action"] == "click", f"action {action_idx} 不是 click"
        dx = abs(actual_x - expected["x"])
        dy = abs(actual_y - expected["y"])
        assert dx <= tolerance and dy <= tolerance, (
            f"点击位置偏差过大: 预期 ({expected['x']}, {expected['y']}), "
            f"实际 ({actual_x}, {actual_y}), delta=({dx}, {dy})"
        )

    def assert_screenshot_similar(self,
                                   action_idx: int,
                                   actual_image: np.ndarray,
                                   threshold: float = 0.95) -> None:
        """验证新截图与录制截图的 SSIM 相似度"""
        expected = self._actions[action_idx]
        assert expected["action"] == "screenshot", f"action {action_idx} 不是 screenshot"
        expected_file = self._record_dir / expected["file"]
        assert expected_file.exists(), f"录制截图不存在: {expected_file}"

        import cv2
        expected_img = cv2.imread(str(expected_file))
        actual_gray = cv2.cvtColor(actual_image, cv2.COLOR_RGB2GRAY)
        expected_gray = cv2.cvtColor(expected_img, cv2.COLOR_BGR2GRAY)

        score = ssim(actual_gray, expected_gray, data_range=255)
        assert score >= threshold, (
            f"截图相似度过低: SSIM={score:.4f} < {threshold}"
        )

    def assert_action_sequence_length(self, expected_count: int) -> None:
        """验证操作序列长度"""
        actual = len(self._actions)
        assert actual == expected_count, (
            f"操作数量不符: 预期 {expected_count}, 实际 {actual}"
        )
