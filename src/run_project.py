"""Прогоняет весь конвейер для одного проекта одной командой.

Запуск из папки npm-generator:
    python src/run_project.py project_01
    python src/run_project.py project_01 --from openings   # начать с этого шага
    python src/run_project.py project_01 --no-blender      # без шагов в Blender

Что где лежит:
- настройки проекта (файлы, слои, ручные значения) — projects/<имя>.json;
- данные проекта — папка data_dir из настроек;
- путь к Blender — переменная окружения BLENDER, иначе стандартный путь Steam;
- готовая НПМ — data/<проект>/output/: FBX, текстуры, validation.txt.

Эталон окон windows_checked.json конвейер только читает и никогда не меняет.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DEFAULT_BLENDER = r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"

STEPS = {
    "contour": "контур здания с топоплана",
    "manual": "ручные значения: высоты, кровля, цвета",
    "annexes": "пристройки и крыльцо",
    "windows": "поиск окон на фасадах",
    "frames": "окна до внешней рамы",
    "evaluate": "сравнение окон с эталоном разметки",
    "openings": "проёмы на стенах модели",
    "preview": "предпросмотр в Blender -> preview.blend",
    "compare": "сравнение модели со сданной НПМ",
    "npm": "генерация НПМ: FBX с текстурами",
    "validate": "проверка НПМ по требованиям",
}
BLENDER_STEPS = {"preview", "compare", "npm", "validate"}


class StepFailed(Exception):
    pass


def load_config(name):
    path = ROOT / "projects" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"Нет настроек проекта: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["data_dir"] = ROOT / cfg["data_dir"]
    cfg["path"] = path
    return cfg


def run(cmd):
    """Запускает команду; вывод идёт прямо в консоль."""
    env = dict(os.environ, PYTHONUTF8="1")
    result = subprocess.run([str(c) for c in cmd], cwd=ROOT, env=env)
    if result.returncode != 0:
        raise StepFailed(f"команда завершилась с ошибкой (код {result.returncode})")


def python(script, *args):
    run([sys.executable, SRC / script, *args])


def blender_command():
    """Как запускать скрипты Blender: blender.exe или Python с модулем bpy."""
    path = os.environ.get("BLENDER", DEFAULT_BLENDER)
    if not Path(path).exists():
        return None
    if "python" in Path(path).name.lower():
        return lambda script, *args: [path, SRC / script, "--", *args]
    # --python-exit-code 1: если скрипт упал, Blender тоже вернёт ошибку
    return lambda script, *args: [path, "--background", "--python-exit-code", "1",
                                  "--python", SRC / script, "--", *args]


def apply_manual(cfg):
    """Дописывает в building.json значения, которые пока задаются вручную."""
    path = cfg["data_dir"] / "building.json"
    building = json.loads(path.read_text(encoding="utf-8"))
    manual = cfg["manual"]
    building["zero_abs"] = manual["zero_abs"]
    building["parts"][0].update(manual["main"])
    auto = [r for r in building.get("review", []) if "ещё не заполнены" not in r]
    building["review"] = manual.get("review", []) + auto
    path.write_text(json.dumps(building, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"zero_abs {manual['zero_abs']}, карниз {manual['main']['z_eave']}, "
          f"конёк {manual['main']['roof']['z_ridge']}")


def do_step(step, cfg, blender):
    data_dir = cfg["data_dir"]
    topo = data_dir / cfg["topo"]["file"]
    facades = data_dir / cfg["facades"]["file"]
    building = data_dir / "building.json"

    if step == "contour":
        python("extract_contour.py", topo, cfg["topo"]["layer"])
    elif step == "manual":
        apply_manual(cfg)
    elif step == "annexes":
        python("extract_annexes.py", topo, cfg["topo"]["layer"])
    elif step == "windows":
        python("find_windows_v2.py", facades)
    elif step == "frames":
        python("extend_frames.py", facades, "raw")
    elif step == "evaluate":
        if (data_dir / "windows_checked.json").exists():
            python("evaluate.py", data_dir, "raw_framed")
        else:
            print("Эталона разметки нет (windows_checked.json) — шаг пропущен")
    elif step == "openings":
        python("place_openings.py", facades, cfg.get("windows_for_model", "checked"))
    elif step == "preview":
        run(blender("preview_blender.py", building))
    elif step == "compare":
        if list((data_dir / "reference").glob("*.fbx")):
            run(blender("compare_reference.py", building))
        else:
            print("Сданной модели нет (папка reference) — шаг пропущен")
    elif step == "npm":
        run(blender("npm_generator.py", building, cfg["path"]))
    elif step == "validate":
        fbx = data_dir / "output" / f"{cfg['npm']['file_name']}.fbx"
        run(blender("validate_npm.py", fbx, cfg["path"]))


def main():
    parser = argparse.ArgumentParser(description="Конвейер ИД -> building.json -> модель")
    parser.add_argument("project", help="имя проекта, например project_01")
    parser.add_argument("--from", dest="start", choices=list(STEPS), default="contour",
                        help="с какого шага начать")
    parser.add_argument("--no-blender", action="store_true", help="пропустить шаги в Blender")
    args = parser.parse_args()

    cfg = load_config(args.project)
    blender = None if args.no_blender else blender_command()
    if not args.no_blender and blender is None:
        print("Blender не найден: задай путь в переменной BLENDER. Шаги в Blender пропускаются.")

    steps = list(STEPS)[list(STEPS).index(args.start):]
    timings = []
    for n, step in enumerate(steps, start=1):
        print(f"\n=== [{n}/{len(steps)}] {step}: {STEPS[step]} ===")
        if step in BLENDER_STEPS and blender is None:
            print("пропущен (без Blender)")
            continue
        t0 = time.time()
        try:
            do_step(step, cfg, blender)
        except StepFailed as err:
            print(f"\nОстановка на шаге «{step}»: {err}")
            print(f"Исправь и продолжи: python src/run_project.py {args.project} --from {step}")
            sys.exit(1)
        timings.append((step, time.time() - t0))

    print("\n=== Готово ===")
    for step, sec in timings:
        print(f"  {step:9} {sec:6.1f} с")
    print(f"Данные: {cfg['data_dir']}")


if __name__ == "__main__":
    main()
