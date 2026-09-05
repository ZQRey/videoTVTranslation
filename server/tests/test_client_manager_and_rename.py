"""
Тесты управления клиентами, контроля аудиопотоков и переименования файлов в плейлисте.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from core.client_manager import ClientDevice, ClientManager
from core.config import ConfigManager, ServerSettings
from core.adb_controller import AdbController, adb_controller
from core.playlist import PlaylistManager
from core.plugins.manager import PluginManager
from core.streamer import StreamOrchestrator
from main import (
    ClientAdbRequest,
    ClientAddRequest,
    ClientAudioControlRequest,
    ClientPoweroffRequest,
    ClientStandbyRequest,
    ClientStreamControlRequest,
    ClientUpdateRequest,
    RenameFileRequest,
    add_manual_client_endpoint,
    client_manager,
    control_client_audio,
    control_client_standby,
    control_client_stream,
    delete_client_endpoint,
    execute_adb_action_endpoint,
    get_adb_status_endpoint,
    get_connected_clients,
    playlist_mgr,
    poweroff_client_endpoint,
    rename_playlist_file_endpoint,
    streamer,
    update_client_meta,
)


class TestClientManager(unittest.IsolatedAsyncioTestCase):
    """Тестирование менеджера подключенных клиентов и управления звуком."""

    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.storage_path = Path(self.tmp_dir) / "test_clients.json"
        self.mgr = ClientManager(storage_path=self.storage_path)

    async def asyncTearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_register_and_heartbeat(self):
        ws_mock = AsyncMock()
        data = {
            "hostname": "WORKSTATION-1",
            "os_info": "Windows 11",
            "screens": ["2276WM (Primary)", "TV-4K"],
            "primary_screen": "2276WM",
            "audio_enabled": True,
        }
        client = await self.mgr.register_or_update(
            client_id="pc-1",
            ip="192.168.1.100",
            data=data,
            websocket=ws_mock,
        )

        self.assertEqual(client.client_id, "pc-1")
        self.assertEqual(client.hostname, "WORKSTATION-1")
        self.assertTrue(client.audio_enabled)

        state = await self.mgr.get_state()
        self.assertEqual(state["total_connected"], 1)
        self.assertEqual(len(state["clients"]), 1)

        # Heartbeat
        old_seen = client.last_seen
        await asyncio.sleep(0.01)
        await self.mgr.update_heartbeat("pc-1")
        self.assertGreater(client.last_seen, old_seen)

    async def test_unregister_client(self):
        ws_mock = AsyncMock()
        data = {
            "hostname": "WORKSTATION-2",
            "os_info": "Linux",
            "screens": ["LG-Display"],
            "primary_screen": "LG-Display",
        }
        await self.mgr.register_or_update(
            client_id="pc-2",
            ip="192.168.1.101",
            data=data,
            websocket=ws_mock,
        )
        state = await self.mgr.get_state()
        self.assertEqual(state["total_connected"], 1)

        await self.mgr.unregister("pc-2")
        state_after = await self.mgr.get_state()
        self.assertEqual(state_after["total_connected"], 0)

    async def test_set_audio_for_single_client(self):
        ws_mock = AsyncMock()
        data = {
            "hostname": "DESKTOP-AUDIO",
            "os_info": "Windows 10",
            "screens": ["Screen1"],
            "primary_screen": "Screen1",
            "audio_enabled": True,
        }
        await self.mgr.register_or_update(
            client_id="pc-audio-1",
            ip="192.168.1.105",
            data=data,
            websocket=ws_mock,
        )

        # Отключаем звук
        res = await self.mgr.set_audio("pc-audio-1", enabled=False)
        self.assertTrue(res)
        client = self.mgr._clients["pc-audio-1"]
        self.assertFalse(client.audio_enabled)
        ws_mock.send_json.assert_called_with({
            "type": "set_audio",
            "audio_enabled": False,
            "enabled": False,
        })

        # Включаем звук обратно
        res = await self.mgr.set_audio("pc-audio-1", enabled=True)
        self.assertTrue(res)
        self.assertTrue(client.audio_enabled)
        ws_mock.send_json.assert_called_with({
            "type": "set_audio",
            "audio_enabled": True,
            "enabled": True,
        })

    async def test_set_audio_for_all_clients(self):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        d1 = {"hostname": "H1", "screens": ["S1"], "primary_screen": "S1"}
        d2 = {"hostname": "H2", "screens": ["S2"], "primary_screen": "S2"}
        await self.mgr.register_or_update("c1", "10.0.0.1", d1, ws1)
        await self.mgr.register_or_update("c2", "10.0.0.2", d2, ws2)

        res = await self.mgr.set_audio("all", enabled=False)
        self.assertTrue(res)
        self.assertFalse(self.mgr._clients["c1"].audio_enabled)
        self.assertFalse(self.mgr._clients["c2"].audio_enabled)

        ws1.send_json.assert_called_with({
            "type": "set_audio",
            "audio_enabled": False,
            "enabled": False,
        })
        ws2.send_json.assert_called_with({
            "type": "set_audio",
            "audio_enabled": False,
            "enabled": False,
        })

    async def test_persistence_and_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Path(tmpdir) / "clients.json"
            mgr1 = ClientManager(storage_path=storage)
            ws = AsyncMock()
            d = {"hostname": "PERSIST-PC", "screens": ["Screen1"]}
            await mgr1.register_or_update("p1", "10.0.0.50", d, ws)
            await mgr1.update_client_meta("p1", custom_name="Главный Холл")
            await mgr1.set_stream_allowed("p1", allowed=False)
            await mgr1.set_standby("p1", standby=True)

            self.assertTrue(storage.exists())

            # Создаем второй менеджер на том же хранилище
            mgr2 = ClientManager(storage_path=storage)
            state = await mgr2.get_state()
            self.assertEqual(len(state["clients"]), 1)
            saved = state["clients"][0]
            self.assertEqual(saved["client_id"], "p1")
            self.assertEqual(saved["custom_name"], "Главный Холл")
            self.assertFalse(saved["stream_allowed"])
            self.assertTrue(saved["standby"])
            self.assertFalse(saved["is_online"])  # оффлайн после перезапуска сервера

    async def test_update_client_meta(self):
        ws = AsyncMock()
        await self.mgr.register_or_update("c1", "10.0.0.1", {"hostname": "H1"}, ws)
        res = await self.mgr.update_client_meta("c1", custom_name="Конференц-зал", os_info="Linux Debian")
        self.assertTrue(res)
        client = self.mgr._clients["c1"]
        self.assertEqual(client.custom_name, "Конференц-зал")
        self.assertEqual(client.os_info, "Linux Debian")
        self.assertEqual(client.os_family, "linux")

        # Несуществующий
        res_none = await self.mgr.update_client_meta("missing", custom_name="Test")
        self.assertFalse(res_none)

    async def test_os_family_detection(self):
        ws = AsyncMock()
        # Windows
        c_win = await self.mgr.register_or_update("win1", "10.0.0.1", {"hostname": "PC-WIN", "os_info": "Windows 11"}, ws)
        self.assertEqual(c_win.os_family, "windows")

        # Linux
        c_lin = await self.mgr.register_or_update("lin1", "10.0.0.2", {"hostname": "PC-LIN", "os_info": "Linux 6.1.0"}, ws)
        self.assertEqual(c_lin.os_family, "linux")

        # Android
        c_and = await self.mgr.register_or_update("and1", "10.0.0.3", {"hostname": "TV-BOX", "os_info": "Android 13"}, ws)
        self.assertEqual(c_and.os_family, "android")

        # macOS / Darwin
        c_mac = await self.mgr.register_or_update("mac1", "10.0.0.4", {"hostname": "MAC-MINI", "os_info": "Darwin 22.4.0"}, ws)
        self.assertEqual(c_mac.os_family, "macos")

        # Unknown
        c_unk = await self.mgr.register_or_update("unk1", "10.0.0.5", {"hostname": "UNKNOWN", "os_info": ""}, ws)
        self.assertEqual(c_unk.os_family, "unknown")

        # Update os_info
        await self.mgr.update_client_meta("unk1", os_info="Android TV Box")
        self.assertEqual(self.mgr._clients["unk1"].os_family, "android")

    async def test_stream_allowed_control(self):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await self.mgr.register_or_update("c1", "10.0.0.1", {"hostname": "H1"}, ws1)
        await self.mgr.register_or_update("c2", "10.0.0.2", {"hostname": "H2"}, ws2)

        # Одиночный
        res = await self.mgr.set_stream_allowed("c1", allowed=False)
        self.assertTrue(res)
        self.assertFalse(self.mgr._clients["c1"].stream_allowed)
        ws1.send_json.assert_called_with({
            "type": "set_stream_allowed",
            "stream_allowed": False,
            "allowed": False,
        })

        # Глобальный "all"
        res_all = await self.mgr.set_stream_allowed("all", allowed=True)
        self.assertTrue(res_all)
        self.assertTrue(self.mgr._clients["c1"].stream_allowed)
        self.assertTrue(self.mgr._clients["c2"].stream_allowed)

    async def test_standby_control(self):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await self.mgr.register_or_update("c1", "10.0.0.1", {"hostname": "H1"}, ws1)
        await self.mgr.register_or_update("c2", "10.0.0.2", {"hostname": "H2"}, ws2)

        res = await self.mgr.set_standby("c1", standby=True)
        self.assertTrue(res)
        self.assertTrue(self.mgr._clients["c1"].standby)
        ws1.send_json.assert_called_with({
            "type": "set_standby",
            "standby": True,
        })

        res_all = await self.mgr.set_standby("all", standby=False)
        self.assertTrue(res_all)
        self.assertFalse(self.mgr._clients["c1"].standby)
        self.assertFalse(self.mgr._clients["c2"].standby)

    async def test_poweroff_client(self):
        ws = AsyncMock()
        await self.mgr.register_or_update("c1", "10.0.0.1", {"hostname": "H1"}, ws)

        # exit_app
        res1 = await self.mgr.poweroff_client("c1", action="exit_app")
        self.assertTrue(res1)
        ws.send_json.assert_called_with({
            "type": "shutdown_device",
            "action": "exit_app",
        })

        # poweroff
        res2 = await self.mgr.poweroff_client("c1", action="poweroff")
        self.assertTrue(res2)
        ws.send_json.assert_called_with({
            "type": "shutdown_device",
            "action": "poweroff",
        })

    async def test_delete_client(self):
        ws = AsyncMock()
        await self.mgr.register_or_update("c1", "10.0.0.1", {"hostname": "H1"}, ws)
        self.assertIn("c1", self.mgr._clients)

        # Удаление
        res = await self.mgr.delete_client("c1")
        self.assertTrue(res)
        self.assertNotIn("c1", self.mgr._clients)

        # Повторное удаление несуществующего
        res2 = await self.mgr.delete_client("c1")
        self.assertFalse(res2)


class TestPlaylistRename(unittest.IsolatedAsyncioTestCase):
    """Тестирование переименования файлов в плейлисте и на диске."""

    async def asyncSetUp(self):
        self.mgr = PlaylistManager()
        self.files = [Path("intro.mp4"), Path("main.mp4"), Path("outro.mp4")]
        await self.mgr.sync_with_scanned(self.files)

    async def test_rename_in_playlist_manager(self):
        state = await self.mgr.get_state()
        self.assertEqual(state["items"], ["intro.mp4", "main.mp4", "outro.mp4"])
        self.assertEqual(state["current_file"], "intro.mp4")

        # Переименовываем текущий трек
        res = await self.mgr.rename_file("intro.mp4", "intro_v2.mp4")
        self.assertTrue(res)

        new_state = await self.mgr.get_state()
        self.assertEqual(new_state["items"], ["intro_v2.mp4", "main.mp4", "outro.mp4"])
        self.assertEqual(new_state["current_file"], "intro_v2.mp4")

        # Переименовываем не текущий трек
        res2 = await self.mgr.rename_file("main.mp4", "feature.mp4")
        self.assertTrue(res2)
        new_state2 = await self.mgr.get_state()
        self.assertEqual(new_state2["items"], ["intro_v2.mp4", "feature.mp4", "outro.mp4"])

        # Попытка переименовать несуществующий файл
        res3 = await self.mgr.rename_file("ghost.mp4", "new_ghost.mp4")
        self.assertFalse(res3)


class TestStreamerRenameMediaFile(unittest.IsolatedAsyncioTestCase):
    """Интеграционные тесты переименования медиафайлов через StreamOrchestrator."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.media_dir = Path(self.temp_dir) / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self.file1 = self.media_dir / "vid1.mp4"
        self.file2 = self.media_dir / "vid2.mp4"
        self.file1.write_bytes(b"content1")
        self.file2.write_bytes(b"content2")

        self.config_file = Path(self.temp_dir) / "settings.json"
        self.cfg_mgr = ConfigManager(self.config_file)
        await self.cfg_mgr.update_settings({"media_dir": str(self.media_dir)})

        self.playlist_mgr = PlaylistManager()
        self.plugin_mgr = PluginManager(self.cfg_mgr)
        self.orchestrator = StreamOrchestrator(
            self.cfg_mgr,
            self.playlist_mgr,
            self.plugin_mgr,
        )

    async def asyncTearDown(self):
        await self.orchestrator.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_rename_media_file_success(self):
        await self.playlist_mgr.sync_with_scanned([self.file1, self.file2])

        new_path = await self.orchestrator.rename_media_file("vid1.mp4", "promo_new.mp4")
        self.assertEqual(new_path.name, "promo_new.mp4")

        # Проверяем на диске
        self.assertFalse((self.media_dir / "vid1.mp4").exists())
        self.assertTrue((self.media_dir / "promo_new.mp4").exists())

        # Проверяем в плейлисте
        state = await self.playlist_mgr.get_state()
        self.assertIn("promo_new.mp4", state["items"])
        self.assertNotIn("vid1.mp4", state["items"])

    async def test_rename_preserves_extension_if_omitted(self):
        await self.playlist_mgr.sync_with_scanned([self.file1, self.file2])

        new_path = await self.orchestrator.rename_media_file("vid2.mp4", "brand_new_title")
        self.assertEqual(new_path.name, "brand_new_title.mp4")
        self.assertTrue((self.media_dir / "brand_new_title.mp4").exists())

    async def test_rename_target_already_exists(self):
        await self.playlist_mgr.sync_with_scanned([self.file1, self.file2])

        with self.assertRaises(FileExistsError):
            await self.orchestrator.rename_media_file("vid1.mp4", "vid2.mp4")

    async def test_rename_source_not_found(self):
        with self.assertRaises(FileNotFoundError):
            await self.orchestrator.rename_media_file("missing.mp4", "anything.mp4")

    async def test_rename_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            await self.orchestrator.rename_media_file("../secret.txt", "hacked.mp4")

        with self.assertRaises(ValueError):
            await self.orchestrator.rename_media_file("vid1.mp4", "../outside.mp4")


class TestApiEndpoints(unittest.IsolatedAsyncioTestCase):
    """Тестирование FastAPI эндпоинтов для клиентов и переименования."""

    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.orig_storage = client_manager._storage_path
        client_manager._storage_path = Path(self.tmp_dir) / "api_clients.json"
        client_manager._clients.clear()

    async def asyncTearDown(self):
        client_manager._storage_path = self.orig_storage
        client_manager._clients.clear()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_get_connected_clients_endpoint(self):
        res = await get_connected_clients()
        self.assertIn("clients", res)
        self.assertIn("total_connected", res)
        self.assertIn("global_audio_enabled", res)

    async def test_client_audio_control_endpoint(self):
        req = ClientAudioControlRequest(client_id="all", audio_enabled=True)
        res = await control_client_audio(req)
        self.assertTrue(res["success"])
        self.assertIn("state", res)

    async def test_update_client_meta_endpoint(self):
        ws = AsyncMock()
        await client_manager.register_or_update("ep-client-1", "10.0.0.99", {"hostname": "EP1"}, ws)

        req = ClientUpdateRequest(client_id="ep-client-1", custom_name="Зал совещаний", os_info="Android TV")
        res = await update_client_meta(req)
        self.assertTrue(res["success"])
        cl = res["state"]["clients"][0]
        self.assertEqual(cl["custom_name"], "Зал совещаний")
        self.assertEqual(cl["os_info"], "Android TV")
        self.assertEqual(cl["os_family"], "android")

    async def test_client_stream_control_endpoint(self):
        req = ClientStreamControlRequest(client_id="all", stream_allowed=True)
        res = await control_client_stream(req)
        self.assertTrue(res["success"])
        self.assertIn("state", res)

    async def test_client_standby_control_endpoint(self):
        req = ClientStandbyRequest(client_id="all", standby=False)
        res = await control_client_standby(req)
        self.assertTrue(res["success"])
        self.assertIn("state", res)

    async def test_client_poweroff_endpoint(self):
        ws = AsyncMock()
        await client_manager.register_or_update("ep-client-2", "10.0.0.98", {"hostname": "EP2"}, ws)
        req = ClientPoweroffRequest(client_id="ep-client-2", action="exit_app")
        res = await poweroff_client_endpoint(req)
        self.assertTrue(res["success"])

    async def test_delete_client_endpoint(self):
        ws = AsyncMock()
        await client_manager.register_or_update("ep-client-3", "10.0.0.97", {"hostname": "EP3"}, ws)
        res = await delete_client_endpoint("ep-client-3")
        self.assertTrue(res["success"])

    async def test_add_manual_client_endpoint(self):
        req = ClientAddRequest(
            ip="192.168.1.188",
            custom_name="ТВ Столовая",
            os_info="Android 12 (TV)",
        )
        res = await add_manual_client_endpoint(req)
        self.assertTrue(res["success"])
        self.assertEqual(res["client"]["custom_name"], "ТВ Столовая")
        self.assertEqual(res["client"]["os_family"], "android")
        self.assertEqual(res["client"]["ip"], "192.168.1.188")

    async def test_adb_status_endpoint(self):
        res = await get_adb_status_endpoint()
        self.assertIn("available", res)
        self.assertIn("path", res)

    async def test_adb_action_endpoint_with_mock(self):
        # Добавляем клиента
        client = await client_manager.add_manual_client("192.168.1.199", "ТВ Тест", "Android")
        
        # Мокаем выполнение ADB действия
        orig_execute = adb_controller.execute_action
        try:
            adb_controller.execute_action = AsyncMock(return_value={
                "success": True,
                "message": "Экран переведен в сон",
                "action": "sleep",
                "output": "OK"
            })
            req = ClientAdbRequest(client_id=client.client_id, action="sleep")
            res = await execute_adb_action_endpoint(req)
            self.assertTrue(res["success"])
            self.assertEqual(res["action"], "sleep")
        finally:
            adb_controller.execute_action = orig_execute


class TestAdbController(unittest.IsolatedAsyncioTestCase):
    """Тестирование функционала ADB контроллера."""

    def test_find_adb_binary(self):
        ctrl = AdbController()
        p = ctrl.find_adb_binary()
        # В комплекте проекта или на хосте adb должен быть найден
        self.assertIsNotNone(p)
        self.assertTrue(ctrl.is_available())
        self.assertIsInstance(ctrl.get_adb_path(), str)

    async def test_connect_device_success(self):
        ctrl = AdbController()
        ctrl.run_adb_raw = AsyncMock(return_value=(0, "connected to 192.168.1.50:5555", ""))
        ok, msg = await ctrl.connect_device("192.168.1.50")
        self.assertTrue(ok)
        self.assertIn("connected to", msg)

    async def test_connect_device_failure(self):
        ctrl = AdbController()
        ctrl.run_adb_raw = AsyncMock(return_value=(1, "", "cannot connect to 192.168.1.50:5555: Connection refused"))
        ok, msg = await ctrl.connect_device("192.168.1.50")
        self.assertFalse(ok)
        self.assertIn("Connection refused", msg)

    async def test_execute_actions_mocked(self):
        ctrl = AdbController()
        ctrl.connect_device = AsyncMock(return_value=(True, "connected"))
        ctrl.run_adb_raw = AsyncMock(return_value=(0, "rebooting", ""))

        # 1. Shutdown
        res = await ctrl.execute_action("192.168.1.50", action="shutdown")
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "shutdown")

        # 2. Sleep
        ctrl.run_adb_raw = AsyncMock(return_value=(0, "", ""))
        res = await ctrl.execute_action("192.168.1.50", action="sleep")
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "sleep")

        # 3. Wakeup
        res = await ctrl.execute_action("192.168.1.50", action="wakeup")
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "wakeup")

        # 4. Reboot
        res = await ctrl.execute_action("192.168.1.50", action="reboot")
        self.assertTrue(res["success"])
        self.assertEqual(res["action"], "reboot")

        # 5. Get Info
        ctrl.run_adb_raw = AsyncMock(return_value=(0, "Mi Box S\n---\n12\n---\n31", ""))
        res = await ctrl.execute_action("192.168.1.50", action="get_info")
        self.assertTrue(res["success"])
        self.assertEqual(res["model"], "Mi Box S")
        self.assertEqual(res["android_version"], "12")
        self.assertIn("Android 12", res["formatted_os"])


class TestExtendedOsDetection(unittest.TestCase):
    """Тестирование распознавания расширенных семейств ОС в ClientDevice."""

    def test_distro_detection(self):
        device_ubuntu = ClientDevice("id1", "1.1.1.1", "host1", "Ubuntu 22.04 LTS (6.5.0)", [], "DP-1")
        self.assertEqual(device_ubuntu.os_family, "linux")

        device_debian = ClientDevice("id2", "1.1.1.2", "host2", "Debian GNU/Linux 12", [], "HDMI-1")
        self.assertEqual(device_debian.os_family, "linux")

        device_win11 = ClientDevice("id3", "1.1.1.3", "host3", "Windows 11 (Build 26200)", [], "DisplayPort-0")
        self.assertEqual(device_win11.os_family, "windows")

        device_win10 = ClientDevice("id4", "1.1.1.4", "host4", "Windows 10 Pro (Build 19045)", [], "DisplayPort-1")
        self.assertEqual(device_win10.os_family, "windows")

        device_android = ClientDevice("id5", "1.1.1.5", "host5", "Android 13 (API 33, Xiaomi TV)", [], "TV-Screen")
        self.assertEqual(device_android.os_family, "android")


if __name__ == "__main__":
    unittest.main()
