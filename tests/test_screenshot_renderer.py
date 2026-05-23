import os
import pytest
from adapter.out.screenshot_renderer import PlaywrightScreenshotRenderer

@pytest.mark.asyncio
async def test_screenshot_renderer_saves_files_on_fallback(tmp_path):
    """
    Prueba que PlaywrightScreenshotRenderer pueda levantar el navegador,
    abrir una página web (usando https://example.com como mock/demo),
    tomar una captura de pantalla y guardar el metadato JSON.
    """
    # Usamos example.com ya que es una URL pública estable y rápida de cargar.
    # Como no contiene el elemento "#chart" ni el marcador "#chart_loaded_signal",
    # validaremos que el mecanismo de fallback a captura de página completa funcione.
    snapshots_dir = os.path.normpath(str(tmp_path))
    renderer = PlaywrightScreenshotRenderer(
        render_url="https://example.com",
        snapshots_dir=snapshots_dir
    )

    result = await renderer.capture(
        symbol="Test Index",
        timeframe="5m",
        trace_id="test-trace-1234-uuid-validation"
    )

    # Validaciones críticas de archivos
    assert result is not None
    assert "artifact_id" in result
    assert "png_path" in result
    assert "json_path" in result
    assert "image_sha256" in result

    png_path = result["png_path"]
    json_path = result["json_path"]

    assert os.path.exists(png_path)
    assert os.path.exists(json_path)
    assert os.path.getsize(png_path) > 0
    assert os.path.getsize(json_path) > 0
    assert len(result["image_sha256"]) == 64

    # Verificar que el subdirectorio tiene el formato adecuado:
    # {snapshots_dir}/test_index/5m/...
    expected_subdir = os.path.join(snapshots_dir, "test_index", "5m")
    assert os.path.dirname(png_path) == expected_subdir
