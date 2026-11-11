"""Материалы CC0 с ambientCG: поиск, скачивание и перевод в формат библиотеки.

Запуск:
    python src/cc0.py search granite            # найти материалы (первые 10)
    python src/cc0.py get PavingStones131       # скачать и добавить в кэш библиотеки
    python src/cc0.py import путь/к/архиву.zip  # добавить архив, скачанный руками

Генератор зовёт resolve(): если в настройках проекта у роли стоит
{"material": "ambientcg:Bricks104"} или {"search": "red brick"}, материал
ищется сначала в библиотеке и кэше, и только потом скачивается.

Кэш: data/cc0_cache/zips/ (архивы) и data/cc0_cache/materials/ (наш формат).
Каждый материал скачивается один раз. API: https://docs.ambientcg.com/api/v2/full_json
Лицензия всех материалов ambientCG — CC0 (можно использовать где угодно).
"""
import io
import json
import re
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cc0_cache"
API = "https://ambientcg.com/api/v2/full_json"
PREFERRED = ("1K-PNG", "1K-JPG", "2K-PNG", "2K-JPG")
MAP_SUFFIXES = {           # какой файл архива какая наша карта
    "d": ("_Color",),
    "n": ("_NormalGL",),
    "r": ("_Roughness",),
    "m": ("_Metalness",),
}
COURSE_M = 0.075           # ряд кладки: кирпич 65 мм + шов 10 мм
DEFAULT_WIDTH_M = 2.0      # если реальный размер неизвестен


def http_get(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": "npm-generator (study project)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def walk(node):
    """Все словари внутри JSON — чтобы не зависеть от точной вложенности ответа API."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def search(query, limit=10, asset_id=None):
    """Материалы ambientCG по запросу: [{id, tags, category, downloads, dimensions}]."""
    params = {"type": "Material", "limit": limit, "sort": "Popular",
              "include": "downloadData,dimensionsData,tagData,displayData"}
    if asset_id:
        params["id"] = asset_id
    else:
        params["q"] = query
    raw = http_get(f"{API}?{urllib.parse.urlencode(params)}")
    data = json.loads(raw)
    assets = data.get("foundAssets") or data.get("assets") or []
    found = []
    for asset in assets:
        downloads = {}
        for d in walk(asset):
            attr = d.get("attribute")
            link = d.get("downloadLink") or d.get("fullDownloadPath") or d.get("rawLink")
            if attr and link:
                downloads[attr] = link
        dims = {k: v for d in walk(asset) for k, v in d.items()
                if k.lower().startswith("dimension") and isinstance(v, (int, float))}
        found.append({
            "id": asset.get("assetId") or asset.get("id"),
            "category": asset.get("category") or asset.get("displayCategory"),
            "tags": asset.get("tags") or [],
            "downloads": downloads,
            "dimensions": dims,
        })
    return found


def download(asset):
    """Скачивает архив материала (1K предпочтительно) в кэш, возвращает путь."""
    for attr in PREFERRED:
        if attr in asset["downloads"]:
            target = CACHE / "zips" / f"{asset['id']}_{attr}.zip"
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                print(f"  скачиваю {asset['id']} ({attr})...")
                target.write_bytes(http_get(asset["downloads"][attr], timeout=300))
            return target
    raise RuntimeError(f"у {asset['id']} нет архива {', '.join(PREFERRED)}; "
                       f"есть: {', '.join(asset['downloads']) or 'ничего'}")


def estimate_size(color_bytes, asset_id, dims):
    """Реальный размер плитки, м: из данных API, иначе по рядам кладки, иначе по умолчанию."""
    from_api = [v for k, v in sorted(dims.items()) if v and k.lower() in ("dimensionx", "dimensiony")]
    if len(from_api) == 2:
        x, y = from_api
        scale = 0.01 if max(x, y) > 20 else 1.0      # сантиметры или метры
        return [round(x * scale, 3), round(y * scale, 3)], "из данных ambientCG"
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(color_bytes)).convert("L")
        w, h = img.size
        aspect = h / w
        if asset_id.lower().startswith("bricks"):
            rows = np.asarray(img, float).mean(axis=1)
            spectrum = np.abs(np.fft.rfft(rows - rows.mean()))
            spectrum[:3] = 0
            courses = int(spectrum[: h // 4].argmax())
            height = courses * COURSE_M
            return [round(height / aspect, 3), round(height, 3)], f"по рядам кладки ({courses} рядов)"
        return [DEFAULT_WIDTH_M, round(DEFAULT_WIDTH_M * aspect, 3)], "по умолчанию"
    except ImportError:
        return [DEFAULT_WIDTH_M, DEFAULT_WIDTH_M], "по умолчанию"


def import_zip(zip_path, asset=None):
    """Архив ambientCG -> материал библиотеки в data/cc0_cache/materials/<id>/."""
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        members = z.namelist()
    color = next((m for m in members if Path(m).stem.endswith("_Color")), None)
    if color is None:
        raise RuntimeError(f"в {zip_path.name} нет карты цвета (_Color)")
    # имя материала — из файлов внутри архива: Bricks104_1K-JPG_Color.jpg -> Bricks104
    asset_id = (asset or {}).get("id") or re.split(r"_\d+K", Path(color).stem)[0]
    name = f"ambientcg_{asset_id}".lower()
    folder = CACHE / "materials" / name
    folder.mkdir(parents=True, exist_ok=True)
    maps, color_bytes = {}, None
    with zipfile.ZipFile(zip_path) as z:
        for member in members:
            stem, ext = Path(member).stem, Path(member).suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg"):
                continue
            for key, suffixes in MAP_SUFFIXES.items():
                if any(stem.endswith(s) for s in suffixes):
                    data = z.read(member)
                    (folder / f"{key}{ext}").write_bytes(data)
                    maps[key] = f"{key}{ext}"
                    if key == "d":
                        color_bytes = data
    size_m, how = estimate_size(color_bytes, asset_id, (asset or {}).get("dimensions", {}))
    tags = [t.lower() for t in (asset or {}).get("tags", [])][:8]
    category = (asset or {}).get("category") or re.sub(r"\d+$", "", asset_id)
    meta = {
        "name": name,
        "title": f"{category} {asset_id} (ambientCG)",
        "tags": ["cc0", category.lower(), *tags],
        "kind": "image",
        "maps": maps,
        "size_m": size_m,
        "size_note": how,
        "stretch": "none",
        "source": f"https://ambientcg.com/view?id={asset_id}",
        "license": "CC0 1.0",
    }
    (folder / "material.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(f"  {name}: карты {', '.join(sorted(maps))}, размер {size_m} м ({how})")
    return name


def resolve(spec, known):
    """Имя материала для настройки роли; при необходимости скачивает с ambientCG.

    spec: {"material": "plaster"} | {"material": "ambientcg:Bricks104"} | {"search": "granite"}
    known: имена, которые уже есть в библиотеке.
    """
    material = spec.get("material", "")
    if material and not material.startswith("ambientcg:"):
        return material
    if material.startswith("ambientcg:"):
        asset_id = material.split(":", 1)[1]
        name = f"ambientcg_{asset_id}".lower()
        if name in known:
            return name
        assets = search(None, limit=1, asset_id=asset_id)
    else:
        query = spec["search"]
        cached = [n for n in known if n.startswith("ambientcg_")
                  and all(w in n or w in " ".join(known[n].get("tags", []))
                          for w in query.lower().split())]
        if cached:
            return cached[0]
        assets = search(query, limit=5)
    if not assets:
        raise RuntimeError(f"ambientCG ничего не нашёл: {spec}")
    return import_zip(download(assets[0]), assets[0])


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "search":
        for a in search(" ".join(sys.argv[2:])):
            print(f"{a['id']:24} {str(a['category']):16} архивы: {', '.join(sorted(a['downloads']))[:60]}")
    elif command == "get":
        found = search(None, limit=1, asset_id=sys.argv[2])
        if not found:
            sys.exit(f"{sys.argv[2]} не найден")
        import_zip(download(found[0]), found[0])
    elif command == "import":
        import_zip(sys.argv[2])
    else:
        print(__doc__)
