"""Библиотека материалов: каталог и получение плитки для атласа.

Где лежат материалы:
- library/materials/<имя>/          — публичные (в git): рецепты кода, CC0;
- data/library_private/materials/<имя>/ — из ваших сданных моделей (не в git).

В каждой папке material.json:
    name      — имя материала (латиница), по нему его зовут в настройках проекта
    title     — описание по-русски
    tags      — теги для поиска: wall, roof, trim, wood, metal...
    kind      — "procedural" (рисует код) или "image" (готовые PNG)
    recipe, params — для procedural: рецепт из textures.py и параметры по умолчанию
    maps      — для image: файлы карт {"d": "d.png", "n": ..., "r": ..., "m": ...}
    size_m    — сколько метров в реальности занимает плитка [ширина, высота]
    stretch   — вдоль какой оси узор можно растягивать: "u" (полосы вдоль),
                "v" (полосы поперёк), "both" (однородный), "none"
    source, license — откуда взят и на каких условиях

Запуск:
    python src/library.py              # список материалов
    python src/library.py --previews   # картинки-превью для публичных материалов
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import textures  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROOTS = [ROOT / "library" / "materials", ROOT / "data" / "library_private" / "materials"]


def catalog():
    """Все материалы: имя -> описание (с путём к папке в поле dir)."""
    found = {}
    for root in ROOTS:
        if not root.exists():
            continue
        for meta_path in sorted(root.glob("*/material.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["dir"] = meta_path.parent
            meta["private"] = root != ROOTS[0]
            found[meta["name"]] = meta
    return found


def resample(img, size):
    """Простое масштабирование до size×size (ближайший пиксель, с усреднением 2×2)."""
    h, w = img.shape[:2]
    ys = (np.arange(size) + 0.5) * h / size
    xs = (np.arange(size) + 0.5) * w / size
    acc = 0
    for dy in (-0.25, 0.25):
        for dx in (-0.25, 0.25):
            yi = np.clip((ys + dy * h / size).astype(int), 0, h - 1)
            xi = np.clip((xs + dx * w / size).astype(int), 0, w - 1)
            acc = acc + img[yi][:, xi]
    return acc / 4


def tile(name, size_px, overrides=None, read_png=None):
    """Плитка материала для атласа: (карты d/n/r/m, описание).

    overrides — параметры проекта (например, свой цвет).
    read_png — функция чтения PNG в массив 0…1 (в Blender своя, вне — Pillow).
    """
    meta = catalog().get(name)
    if meta is None:
        raise KeyError(f"материала {name} нет в библиотеке")
    params = {**meta.get("params", {}), **(overrides or {})}
    if meta["kind"] == "procedural":
        maps = textures.render(meta["recipe"], size_px, meta["size_m"][0], **params)
    else:
        read_png = read_png or read_png_pillow
        maps = {}
        for key, file in meta["maps"].items():
            img = read_png(meta["dir"] / file)
            maps[key] = resample(img, size_px)
        if "color" in params:                       # перекраска под цвет проекта
            mean = maps["d"].reshape(-1, 3).mean(0)
            maps["d"] = np.clip(maps["d"] * (textures.hex_rgb(params["color"]) / (mean + 1e-6)), 0, 1)
    for key, default in (("n", (0.5, 0.5, 1.0)), ("r", (0.9,) * 3), ("m", (0.0,) * 3)):
        maps.setdefault(key, np.ones((size_px, size_px, 3)) * np.array(default))
    return maps, meta


def read_png_pillow(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB"), dtype=float) / 255


def save_png(arr, path):
    from PIL import Image
    Image.fromarray((np.clip(arr, 0, 1) * 255).astype("uint8")).save(path)


def write_previews():
    """Превью d и n для публичных материалов + таблица library/CATALOG.md."""
    rows = ["| Материал | Описание | Теги | Размер, м | Источник | Лицензия |",
            "|---|---|---|---|---|---|"]
    for name, meta in catalog().items():
        if meta["private"]:
            continue
        maps, _ = tile(name, 256)
        save_png(maps["d"], meta["dir"] / "preview_d.png")
        save_png(maps["n"], meta["dir"] / "preview_n.png")
        size = "×".join(str(v) for v in meta["size_m"])
        rows.append(f"| `{name}` | {meta['title']} | {', '.join(meta['tags'])} | {size} | "
                    f"{meta['source']} | {meta['license']} |")
    (ROOT / "library" / "CATALOG.md").write_text(
        "# Каталог материалов\n\nПревью — в папке каждого материала (preview_d.png).\n\n"
        + "\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--previews" in sys.argv:
        write_previews()
        print("Превью и library/CATALOG.md обновлены")
    for name, meta in catalog().items():
        where = "частный" if meta["private"] else "публичный"
        print(f"{name:40} {meta['kind']:10} {where:10} {meta['title']}")
