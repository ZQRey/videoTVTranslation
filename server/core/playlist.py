"""
Менеджер динамической очереди воспроизведения.
Строго реализует логику:
1. Новые файлы вставляются сразу за текущим играющим файлом.
2. Удаленные файлы немедленно исключаются из очереди с корректировкой указателя.
3. Очередь зацикливается по завершении последнего файла.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("stream_server.playlist")


class PlaylistManager:
    """
    Потокобезопасный менеджер очереди медиафайлов.
    """

    def __init__(self):
        self._items: List[Path] = []
        self._current_index: int = -1
        self._lock = asyncio.Lock()

    async def sync_with_scanned(self, scanned_files: List[Path]) -> None:
        """
        Синхронизирует очередь со списком файлов, обнаруженных сканером.

        :param scanned_files: список актуальных файлов на диске (отсортированный)
        """
        async with self._lock:
            # Преобразуем к resolved путям для точного сравнения
            scanned_set = {f.resolve() for f in scanned_files}
            current_resolved = [f.resolve() for f in self._items]

            # 1. Поиск удаленных файлов
            removed_indices = [
                i for i, f in enumerate(current_resolved) if f not in scanned_set
            ]

            if removed_indices:
                new_items: List[Path] = []
                new_current_idx = self._current_index

                for idx, item in enumerate(self._items):
                    if idx in removed_indices:
                        logger.info(
                            f"Файл {item.name} удален с диска — исключен из очереди."
                        )
                        # Если удаленный файл находился до или на текущей позиции, сдвигаем указатель
                        if idx < self._current_index:
                            new_current_idx -= 1
                        elif idx == self._current_index:
                            # Удален текущий файл
                            logger.warning(
                                f"Текущий файл {item.name} был удален во время трансляции."
                            )
                    else:
                        new_items.append(item)

                self._items = new_items
                if not self._items:
                    self._current_index = -1
                else:
                    # Нормализуем индекс в границах списка
                    self._current_index = max(
                        0, min(new_current_idx, len(self._items) - 1)
                    )

            # 2. Поиск новых файлов
            existing_set = {f.resolve() for f in self._items}
            new_files = [f for f in scanned_files if f.resolve() not in existing_set]

            if new_files:
                logger.info(
                    f"Обнаружено новых файлов: {len(new_files)} {[f.name for f in new_files]}"
                )
                if not self._items:
                    # Очередь была пустой — новые файлы становятся всей очередью
                    self._items = list(new_files)
                    self._current_index = 0
                else:
                    # Вставляем новые файлы СРАЗУ ЗА ТЕКУЩИМ воспроизводимым файлом
                    insert_pos = self._current_index + 1
                    for offset, new_f in enumerate(new_files):
                        self._items.insert(insert_pos + offset, new_f)
                    logger.info(
                        f"Новые файлы вставлены в позицию {insert_pos} сразу за текущим файлом."
                    )

    async def get_current(self) -> Optional[Path]:
        """Возвращает текущий воспроизводимый файл без смещения указателя."""
        async with self._lock:
            if 0 <= self._current_index < len(self._items):
                return self._items[self._current_index]
            return None

    async def advance(self) -> Optional[Path]:
        """
        Переходит к следующему треку в очереди с зацикливанием.
        Возвращает путь к новому треку или None, если очередь пуста.
        """
        async with self._lock:
            if not self._items:
                self._current_index = -1
                return None

            # Зацикливание по кругу
            self._current_index = (self._current_index + 1) % len(self._items)
            next_track = self._items[self._current_index]
            logger.info(
                f"Переход к следующему треку: [{self._current_index + 1}/{len(self._items)}] {next_track.name}"
            )
            return next_track

    async def skip(self) -> Optional[Path]:
        """Принудительный пропуск текущего трека."""
        return await self.advance()

    async def previous(self) -> Optional[Path]:
        """
        Переходит к предыдущему треку в очереди с зацикливанием.
        Возвращает путь к новому треку или None, если очередь пуста.
        """
        async with self._lock:
            if not self._items:
                self._current_index = -1
                return None

            # Циклический переход назад
            self._current_index = (self._current_index - 1) % len(self._items)
            prev_track = self._items[self._current_index]
            logger.info(
                f"Переход к предыдущему треку: [{self._current_index + 1}/{len(self._items)}] {prev_track.name}"
            )
            return prev_track

    async def set_current_by_name(self, filename: str) -> Optional[Path]:
        """
        Устанавливает указатель воспроизведения на файл с указанным именем.
        Возвращает путь к файлу или None, если файл не найден в очереди.
        """
        async with self._lock:
            for idx, item in enumerate(self._items):
                if item.name == filename:
                    self._current_index = idx
                    logger.info(
                        f"Ручной выбор трека: [{self._current_index + 1}/{len(self._items)}] {item.name}"
                    )
                    return item
            logger.warning(f"Файл {filename} не найден в очереди воспроизведения.")
            return None

    async def move_item(self, from_index: int, to_index: int) -> bool:
        """
        Перемещает элемент очереди с позиции from_index на to_index.
        Корректирует указатель текущего воспроизводимого файла.
        """
        async with self._lock:
            if not (0 <= from_index < len(self._items)) or not (0 <= to_index < len(self._items)):
                return False

            if from_index == to_index:
                return True

            current_item = (
                self._items[self._current_index]
                if 0 <= self._current_index < len(self._items)
                else None
            )

            item = self._items.pop(from_index)
            self._items.insert(to_index, item)

            if current_item and current_item in self._items:
                self._current_index = self._items.index(current_item)

            logger.info(f"Элемент {item.name} перемещен с позиции {from_index} на {to_index}.")
            return True

    async def reorder(self, filenames: List[str]) -> bool:
        """
        Задает новый порядок воспроизведения на основе переданного списка имен файлов.
        """
        async with self._lock:
            current_map = {f.name: f for f in self._items}
            if set(filenames) != set(current_map.keys()) or len(filenames) != len(self._items):
                logger.warning("Передан некорректный список файлов для изменения порядка очереди.")
                return False

            current_item = (
                self._items[self._current_index]
                if 0 <= self._current_index < len(self._items)
                else None
            )
            self._items = [current_map[name] for name in filenames]

            if current_item and current_item in self._items:
                self._current_index = self._items.index(current_item)

            logger.info("Порядок очереди воспроизведения успешно обновлен.")
            return True

    async def remove_file(self, filename: str) -> Optional[Path]:
        """
        Удаляет файл из очереди по имени и корректирует текущий индекс.
        Возвращает путь к удаленному файлу или None, если файл не найден.
        """
        async with self._lock:
            target_idx = None
            for idx, item in enumerate(self._items):
                if item.name == filename:
                    target_idx = idx
                    break

            if target_idx is None:
                return None

            removed_item = self._items.pop(target_idx)
            logger.info(f"Файл {removed_item.name} удален из очереди воспроизведения.")

            if not self._items:
                self._current_index = -1
            else:
                if target_idx < self._current_index:
                    self._current_index -= 1
                elif target_idx == self._current_index:
                    # Текущий файл удален — оставляем указатель на той же позиции или сдвигаем в конец
                    self._current_index = self._current_index % len(self._items)

            return removed_item

    async def rename_file(self, old_name: str, new_name: str) -> bool:
        """
        Переименовывает файл в очереди с сохранением текущего порядка и индекса.
        """
        async with self._lock:
            found = False
            for idx, item in enumerate(self._items):
                if item.name == old_name:
                    new_path = item.with_name(new_name)
                    self._items[idx] = new_path
                    found = True
                    logger.info(f"Файл в очереди переименован: {old_name} -> {new_name}")
                    break
            return found

    async def get_state(self) -> Dict[str, Any]:
        """Возвращает снимок состояния очереди для дашборда и телеметрии."""
        async with self._lock:
            current_track = (
                self._items[self._current_index].name
                if (0 <= self._current_index < len(self._items))
                else None
            )
            playlist_names = [f.name for f in self._items]
            return {
                "total": len(self._items),
                "current_index": self._current_index,
                "current_file": current_track,
                "items": playlist_names,
            }
