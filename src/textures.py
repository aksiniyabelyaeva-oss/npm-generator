"""Материалы, которые рисует код: бесшовные плитки с картами d, n, r, m.

Работает только на numpy, поэтому годится и в обычном Python, и внутри Blender.
Каждый «рецепт» получает размер плитки в пикселях и метрах и параметры
(цвет, шаг досок...) и возвращает словарь карт — массивы size×size×3
со значениями 0…1:
- d — цвет (sRGB), n — нормали, r — шероховатость, m — металличность.

Бесшовность: весь шум и узоры периодичны по размеру плитки, а нормали
считаются с заворотом краёв (np.roll), поэтому плитки стыкуются без швов.
"""
import numpy as np


def hex_rgb(value):
    h = value.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])


def periodic_noise(size, scale_px, seed=0, stretch=(1.0, 1.0)):
    """Бесшовный шум: случайные частоты, обрезанные выше 1/scale_px.

    stretch растягивает зерно: (8, 1) — волокна вдоль строки, как у дерева.
    """
    rng = np.random.default_rng(seed)
    spectrum = rng.normal(size=(size, size)) + 1j * rng.normal(size=(size, size))
    fy = np.fft.fftfreq(size)[:, None] * stretch[1]
    fx = np.fft.fftfreq(size)[None, :] * stretch[0]
    radius = np.sqrt(fx ** 2 + fy ** 2) * scale_px
    spectrum *= np.exp(-radius ** 2)
    field = np.real(np.fft.ifft2(spectrum))
    field -= field.mean()
    return field / (np.abs(field).max() + 1e-9)


def normal_from_height(height, strength):
    """Карта нормалей из карты высот (с заворотом краёв — без швов)."""
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) / 2
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) / 2
    n = np.dstack([-dx * strength, dy * strength, np.ones_like(height)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return n * 0.5 + 0.5


def maps(color, height, strength, roughness, metallic=0.0):
    size = height.shape[0]
    return {
        "d": np.clip(color, 0, 1),
        "n": normal_from_height(height, strength),
        "r": np.full((size, size, 3), roughness) if np.isscalar(roughness)
        else np.repeat(np.clip(roughness, 0, 1)[..., None], 3, axis=2),
        "m": np.full((size, size, 3), metallic),
    }


def solid(size, size_m, color="#F2EDE0", roughness=0.85, metallic=0.0, noise=0.02, seed=1):
    """Ровная окраска с едва заметной неровностью."""
    grain = periodic_noise(size, 6, seed)
    base = hex_rgb(color)[None, None, :] * (1 + noise * grain[..., None])
    return maps(base, grain * 0.2, 1.0, roughness, metallic)


def plaster(size, size_m, color="#A38F7A", roughness=0.92, seed=2):
    """Штукатурка: крупные пятна и мелкая фактура."""
    big = periodic_noise(size, size / 6, seed)
    small = periodic_noise(size, 2, seed + 1)
    base = hex_rgb(color)[None, None, :] * (1 + 0.025 * big[..., None] + 0.03 * small[..., None])
    return maps(base, small * 0.5 + big * 0.2, 2.0, roughness)


def wood_siding(size, size_m, color="#D1AB6E", board_m=0.15, roughness=0.8, seed=3):
    """Горизонтальная доска: полосы шагом board_m, тень у нижнего края, волокна вдоль."""
    px_per_m = size / size_m
    boards = max(1, round(size_m / board_m))              # целое число досок — без шва
    pitch = size / boards
    y = np.arange(size)[:, None] + np.zeros((1, size))
    phase = (y % pitch) / pitch                           # 0 — верх доски, 1 — низ
    rng = np.random.default_rng(seed)
    tint = rng.normal(0, 0.035, boards)[(y // pitch).astype(int) % boards]
    grain = periodic_noise(size, 3, seed + 1, stretch=(0.08, 1.0))
    shade = 1 - 0.35 * np.clip((phase - 0.85) / 0.15, 0, 1)   # тень под нахлёстом
    base = hex_rgb(color)[None, None, :] * (shade * (1 + tint + 0.04 * grain))[..., None]
    height = -np.clip((phase - 0.85) / 0.15, 0, 1) * 2 + grain * 0.1
    return maps(base, height, 2.5 * px_per_m / 128, roughness)


def standing_seam(size, size_m, color="#454A4F", seam_m=0.5, roughness=0.45,
                  metallic=0.6, seed=4):
    """Фальцевая кровля: вертикальные рёбра шагом seam_m, гладкий металл между ними."""
    px_per_m = size / size_m
    seams = max(1, round(size_m / seam_m))
    pitch = size / seams
    x = np.zeros((size, 1)) + np.arange(size)[None, :]
    dist = np.abs(((x % pitch) / pitch) - 0.5) * 2         # 1 — на ребре, 0 — середина листа
    ridge = np.clip((dist - 0.93) / 0.07, 0, 1)
    wave = periodic_noise(size, size / 10, seed)
    base = hex_rgb(color)[None, None, :] * (1 + 0.12 * ridge + 0.02 * wave)[..., None]
    return maps(base, ridge * 2 + wave * 0.1, 2.0 * px_per_m / 128, roughness, metallic)


def glass(size, size_m, color="#2E3A42", roughness=0.08, metallic=0.0, seed=5):
    """Тёмное стекло с лёгким отражением неба сверху."""
    y = np.linspace(0, 1, size)[:, None] + np.zeros((1, size))
    sky = 0.12 * (1 - y)
    base = hex_rgb(color)[None, None, :] + sky[..., None] * 0.5
    flat = np.zeros((size, size))
    return maps(base, flat, 0.0, roughness, metallic)


RECIPES = {
    "solid": solid,
    "plaster": plaster,
    "wood_siding": wood_siding,
    "standing_seam": standing_seam,
    "glass": glass,
}


def render(recipe, size, size_m, **params):
    """Плитка по рецепту: словарь карт d, n, r, m."""
    return RECIPES[recipe](size, size_m, **params)
