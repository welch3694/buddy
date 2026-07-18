"""Tests for camera device selection and session overrides."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from buddy_tools.media.camera import (
    _ENV_CAMERA_DEVICE,
    _ENV_CAMERA_NAME,
    DEFAULT_DEVICE_INDEX,
    clear_session_camera_override,
    execute_list_cameras_tool,
    execute_set_active_camera_tool,
    get_session_camera_override,
    resolve_camera_device_index,
    resolve_camera_selector,
    set_session_camera_override,
)


class ResolveCameraDeviceTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop(_ENV_CAMERA_DEVICE, None)
        os.environ.pop(_ENV_CAMERA_NAME, None)
        clear_session_camera_override()

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
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            self.assertEqual(resolve_camera_device_index(), 3)

    def test_name_env_case_insensitive(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "obs virtual camera"
        devices = [(3, "OBS Virtual Camera")]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            self.assertEqual(resolve_camera_device_index(), 3)

    def test_name_env_unique_partial_match(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "OBS Virtual"
        devices = [
            (2, "OBSBOT Virtual Camera"),
            (3, "OBS Virtual Camera"),
        ]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            self.assertEqual(resolve_camera_device_index(), 3)

    def test_name_env_ambiguous_partial_match(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "Virtual Camera"
        devices = [
            (2, "OBSBOT Virtual Camera"),
            (3, "OBS Virtual Camera"),
        ]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                resolve_camera_device_index()

    def test_name_env_not_found(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "Missing Camera"
        devices = [(0, "Webcam")]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            with self.assertRaisesRegex(ValueError, "not found"):
                resolve_camera_device_index()

    def test_name_env_takes_precedence_over_device(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "OBS Virtual Camera"
        os.environ[_ENV_CAMERA_DEVICE] = "0"
        devices = [(3, "OBS Virtual Camera")]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            self.assertEqual(resolve_camera_device_index(), 3)

    def test_session_override_takes_precedence_over_env(self) -> None:
        os.environ[_ENV_CAMERA_NAME] = "OBS Virtual Camera"
        os.environ[_ENV_CAMERA_DEVICE] = "0"
        set_session_camera_override(2, "USB Capture")
        self.assertEqual(resolve_camera_device_index(), 2)

    def test_session_override_takes_precedence_over_default(self) -> None:
        set_session_camera_override(4, "Desk Cam")
        self.assertEqual(resolve_camera_device_index(), 4)


class ResolveCameraSelectorTests(unittest.TestCase):
    def test_resolve_by_name(self) -> None:
        devices = [(3, "OBS Virtual Camera")]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            index, label = resolve_camera_selector(camera_name="OBS Virtual Camera")
        self.assertEqual(index, 3)
        self.assertEqual(label, "OBS Virtual Camera")

    def test_resolve_by_index(self) -> None:
        devices = [(1, "Webcam")]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            index, label = resolve_camera_selector(device_index=1)
        self.assertEqual(index, 1)
        self.assertEqual(label, "Webcam")

    def test_requires_selector(self) -> None:
        with self.assertRaisesRegex(ValueError, "Provide camera_name or device_index"):
            resolve_camera_selector()


class CameraToolTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_session_camera_override()

    def test_set_active_camera_by_name(self) -> None:
        devices = [(3, "OBS Virtual Camera")]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            result = execute_set_active_camera_tool({"camera_name": "OBS Virtual Camera"})

        self.assertIn("OBS Virtual Camera", result.output)
        self.assertIn("device 3", result.output)
        self.assertEqual(get_session_camera_override(), (3, "OBS Virtual Camera"))

    def test_set_active_camera_by_index(self) -> None:
        devices = [(2, "Desk Cam")]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            result = execute_set_active_camera_tool({"device_index": 2})

        self.assertIn("device 2", result.output)
        self.assertEqual(get_session_camera_override(), (2, "Desk Cam"))

    def test_set_active_camera_invalid_name(self) -> None:
        devices = [(0, "Webcam")]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            result = execute_set_active_camera_tool({"camera_name": "Missing Camera"})

        self.assertTrue(result.output.startswith("Error:"))
        self.assertIn("not found", result.output)
        self.assertIsNone(get_session_camera_override())

    def test_set_active_camera_requires_selector(self) -> None:
        result = execute_set_active_camera_tool({})
        self.assertTrue(result.output.startswith("Error:"))
        self.assertIn("provide camera_name or device_index", result.output)

    def test_list_cameras_includes_devices_and_active_camera(self) -> None:
        set_session_camera_override(3, "OBS Virtual Camera")
        devices = [
            (0, "Webcam"),
            (3, "OBS Virtual Camera"),
        ]
        with mock.patch("buddy_tools.media.camera._list_camera_devices", return_value=devices):
            result = execute_list_cameras_tool()

        self.assertIn("0: Webcam", result.output)
        self.assertIn("3: OBS Virtual Camera", result.output)
        self.assertIn("Active camera: OBS Virtual Camera (device 3)", result.output)


if __name__ == "__main__":
    unittest.main()
