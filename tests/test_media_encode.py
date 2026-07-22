"""Tests for dual JPEG encode (preview + full-resolution delivery) (#173)."""

from __future__ import annotations

import base64
import unittest

import cv2
import numpy as np

from buddy_tools.media.encode import (
    DELIVERY_JPEG_QUALITY,
    PREVIEW_JPEG_QUALITY,
    encode_preview_and_delivery,
)


class EncodePreviewAndDeliveryTests(unittest.TestCase):
    def test_preview_width_capped_delivery_keeps_native(self) -> None:
        frame = np.zeros((600, 1600, 3), dtype=np.uint8)
        frame[:, :] = (10, 20, 30)
        captured = encode_preview_and_delivery(frame, max_width=768)

        self.assertEqual(captured.width, 1600)
        self.assertEqual(captured.height, 600)
        self.assertTrue(captured.preview_data_uri.startswith("data:image/jpeg;base64,"))
        self.assertGreater(len(captured.delivery_jpeg), 0)

        preview_b64 = captured.preview_data_uri.split(",", 1)[1]
        preview_bytes = base64.b64decode(preview_b64)
        preview = cv2.imdecode(np.frombuffer(preview_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        delivery = cv2.imdecode(
            np.frombuffer(captured.delivery_jpeg, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        assert preview is not None
        assert delivery is not None

        self.assertEqual(preview.shape[1], 768)
        self.assertEqual(preview.shape[0], 288)
        self.assertEqual(delivery.shape[1], 1600)
        self.assertEqual(delivery.shape[0], 600)

    def test_narrow_frame_skips_preview_resize(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        captured = encode_preview_and_delivery(frame, max_width=768)
        preview_b64 = captured.preview_data_uri.split(",", 1)[1]
        preview = cv2.imdecode(
            np.frombuffer(base64.b64decode(preview_b64), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        assert preview is not None
        self.assertEqual(preview.shape[1], 200)
        self.assertEqual(preview.shape[0], 100)

    def test_delivery_uses_higher_quality_than_preview(self) -> None:
        frame = np.random.randint(0, 255, (480, 1280, 3), dtype=np.uint8)
        captured = encode_preview_and_delivery(frame, max_width=768)
        ok_preview, preview_hi = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY],
        )
        ok_delivery, delivery_hi = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, DELIVERY_JPEG_QUALITY],
        )
        self.assertTrue(ok_preview and ok_delivery)
        # Same native frame: delivery buffer should match quality-95 encode size closely
        self.assertEqual(len(captured.delivery_jpeg), len(delivery_hi.tobytes()))
        self.assertGreater(len(captured.delivery_jpeg), len(preview_hi.tobytes()))


if __name__ == "__main__":
    unittest.main()
