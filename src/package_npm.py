"""Упаковка НПМ в архив для подачи.

Запуск: python src/package_npm.py projects/project_01.json

По распоряжению ДИТ и ДГП от 18.08.2026 № ДГП-Р-56/26/64-16-473/26, прил. 1:
- архив ZIP до 1 ГБ, без битых файлов (т2 п. 1.1);
- внутри от 2 до 21 FBX: один файл благоустройства *_Ground.fbx и 1…20 файлов ОКС *_NN.fbx
  с встроенными текстурами (т2 п. 1.2, рис. 2);
- имя архива: префикс_Address.zip, например 0313_ProezdNansena_ZU_8.zip (т2 п. 10.3.1).

Подпись УКЭП руководителя ставится отдельным файлом с тем же именем — это делает человек.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_ZIP = 1024 ** 3


if __name__ == "__main__":
    cfg_path = Path(sys.argv[1])
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    out_dir = ROOT / cfg["data_dir"] / "output"
    file_name = cfg["npm"]["file_name"]                      # 0507_Address_01
    base = re.sub(r"_\d{2}$", "", file_name)                 # 0507_Address
    buildings = sorted(out_dir.glob(f"{base}_[0-9][0-9].fbx"))
    ground = out_dir / f"{base}_Ground.fbx"

    notes = []
    if not buildings:
        sys.exit(f"Нет FBX зданий {base}_NN.fbx в {out_dir}")
    if len(buildings) > 20:
        notes.append(f"файлов ОКС {len(buildings)}, допустимо до 20 (т2 п. 1.2)")
    numbers = [int(p.stem[-2:]) for p in buildings]
    if numbers != list(range(1, len(numbers) + 1)):
        notes.append(f"номера файлов ОКС {numbers}: нужно подряд с 01 (т2 п. 10.3.2)")
    if not ground.exists():
        notes.append(f"нет файла благоустройства {ground.name} — он обязателен (т2 п. 1.6)")

    zip_path = out_dir / f"{base}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in buildings + ([ground] if ground.exists() else []):
            z.write(p, p.name)
    with zipfile.ZipFile(zip_path) as z:
        broken = z.testzip()
    size = zip_path.stat().st_size
    if broken:
        notes.append(f"битый файл в архиве: {broken} (т2 п. 1.1)")
    if size > MAX_ZIP:
        notes.append(f"архив {size / 2 ** 20:.0f} МБ, больше 1 ГБ (т2 п. 1.1)")

    print(f"Архив: {zip_path.name}, {size / 2 ** 20:.1f} МБ, файлов: "
          f"{len(buildings) + ground.exists()} ({', '.join(p.name for p in buildings)}"
          f"{', ' + ground.name if ground.exists() else ''})")
    for n in notes:
        print(f"  [ !! ] {n}")
    print("Подпись УКЭП руководителя — отдельным файлом с тем же именем, ставит человек.")
