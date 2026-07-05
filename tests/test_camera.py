"""Tests for camera device selection."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from buddy_tools.camera import (
    _ENV_CAMERA_DEVICE,
    _ENV_CAMERA_NAME,
    DEFAULT_DEVICE_INDEX,
    resolve_camera_device_index,
)


class ResolveCameraDeviceTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop(_ENV_CAMERA_DEVICE, None)
        os.environ.pop(_ENV_CAMERA_NAME, None)

    def test_defaults_to_zero_when_unconfigured(self) -> None:
        self.assertEqual(resolve_camera_device_index(), DEFAULT_DEVICE_INDEX)

    def test_device_env_parses_integer(self) -> None:
        os.environ[_ENV_CAMERA_DEVICE] = "3"
        self.assertEqual(resolve_camera_device_index(), 3)

    def test_device_env_rejects_non_integer(self) -> None:
        os.environ[_ENV_CAMERA_DEVICE] = "abc"
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            resolve_camera_device_index()

    def test_device_env_rejects_negative(self) -> None:
        os.environ[_ENV_CAMERA_DEVICE] = "-1"
        with self.assertRaisesRegex(ValueError, "non-negative"):
            resolve_camera_device_index()

    def test_name_env_exact_match(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "OBS Virtual Camera"
        devices = [
            (0, "OBSBOT Tiny 2 Lite StreamCamera"),
            (3, "OBS Virtual Camera"),
        ]
        with mock.patch("buddy_tools.camera._list_camera_devices", return_value=devices):
            self.assertEqual(resolve_camera_device_index(), 3)

    def test_name_env_case_insensitive(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "obs virtual camera"
        devices = [(3, "OBS Virtual Camera")]
        with mock.patch("buddy_tools.camera._list_camera_devices", return_value=devices):
            self.assertEqual(resolve_camera_device_index(), 3)

    def test_name_env_unique_partial_match(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "OBS Virtual"
        devices = [
            (2, "OBSBOT Virtual Camera"),
            (3, "OBS Virtual Camera"),
        ]
        with mock.patch("buddy_tools.camera._list_camera_devices", return_value=devices):
            self.assertEqual(resolve_camera_device_index(), 3)

    def test_name_env_ambiguous_partial_match(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "Virtual Camera"
        devices = [
            (2, "OBSBOT Virtual Camera"),
            (3, "OBS Virtual Camera"),
        ]
        with mock.patch("buddy_tools.camera._list_camera_devices", return_value=devices):
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                resolve_camera_device_index()

    def test_name_env_not_found(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "Missing Camera"
        devices = [(0, "Webcam")]
        with mock.patch("buddy_tools.camera._list_camera_devices", return_value=devices):
            with self.assertRaisesRegex(ValueError, "not found"):
                resolve_camera_device_index()

    def test_name_env_takes_precedence_over_device(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "OBS Virtual Camera"
        os.environ[_ENV_CAMERA_DEVICE] = "0"
        devices = [(3, "OBS Virtual Camera")]
        with mock.patch("buddy_tools.camera._list_camera_devices", return_value=devices):
            self.assertEqual(resolve_camera_device_index(), 3)


if __name__ == "__main__":
    unittest.main()
