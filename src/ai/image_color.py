"""Local pixel-based dominant color analysis."""

from pathlib import Path

from PIL import Image


def rgb_to_hex(
    rgb: tuple[int, int, int],
) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def extract_dominant_colors(
    file_path: str,
    count: int = 5,
    sample_size: int = 256,
) -> list[dict]:
    """
    이미지의 실제 픽셀을 기반으로 대표 색상을 추출한다.

    반환 예:
    [
        {
            "rgb": [24, 103, 65],
            "hex": "#186741",
            "ratio": 35.21
        }
    ]
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"이미지를 찾을 수 없습니다: {file_path}"
        )

    with Image.open(path) as original:
        # GIF는 첫 프레임
        if getattr(original, "is_animated", False):
            original.seek(0)

        # 투명 배경 처리
        if original.mode in ("RGBA", "LA"):
            rgba = original.convert("RGBA")

            background = Image.new(
                "RGBA",
                rgba.size,
                (255, 255, 255, 255),
            )

            background.alpha_composite(rgba)

            image = background.convert("RGB")

        else:
            image = original.convert("RGB")

        # 계산량만 줄이기 위한 축소.
        # 원본 비율은 유지된다.
        image.thumbnail(
            (sample_size, sample_size)
        )

        # 유사한 색상을 대표 색상으로 군집화
        quantized = image.quantize(
            colors=count,
            method=Image.Quantize.MEDIANCUT,
        )

        palette = quantized.getpalette()
        color_counts = quantized.getcolors()

        if not palette or not color_counts:
            return []

        total_pixels = sum(
            pixel_count
            for pixel_count, _ in color_counts
        )

        results = []

        sorted_colors = sorted(
            color_counts,
            key=lambda item: item[0],
            reverse=True,
        )

        for pixel_count, palette_index in sorted_colors[:count]:
            start = palette_index * 3

            rgb_values = palette[
                start:start + 3
            ]

            if len(rgb_values) != 3:
                continue

            rgb = (
                int(rgb_values[0]),
                int(rgb_values[1]),
                int(rgb_values[2]),
            )

            ratio = (
                pixel_count
                / total_pixels
                * 100
                if total_pixels
                else 0.0
            )

            results.append(
                {
                    "rgb": list(rgb),
                    "hex": rgb_to_hex(rgb),
                    "ratio": round(
                        ratio,
                        2,
                    ),
                }
            )

        return results