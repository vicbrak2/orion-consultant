import os
import uuid
import logging
import hashlib
import json
from datetime import datetime, timezone
from playwright.async_api import async_playwright

logger = logging.getLogger("orion.screenshot_renderer")

class PlaywrightScreenshotRenderer:
    def __init__(self, render_url: str, snapshots_dir: str):
        self.render_url = render_url
        self.snapshots_dir = snapshots_dir
        os.makedirs(self.snapshots_dir, exist_ok=True)

    async def capture(self, symbol: str, timeframe: str, trace_id: str) -> dict | None:
        """
        Captura de pantalla asíncrona del gráfico utilizando Playwright.
        Guarda la imagen en formato PNG y un metadato JSON complementario.
        """
        try:
            artifact_id = str(uuid.uuid4())
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            
            # Nombre de archivo normalizado
            safe_symbol = symbol.replace(" ", "_").replace("-", "_").lower()
            file_name = f"{safe_symbol}_{timeframe}_{timestamp}_{trace_id[:8]}_{artifact_id[:8]}.png"
            
            # Crear directorios correspondientes
            dir_path = os.path.join(self.snapshots_dir, safe_symbol, timeframe)
            os.makedirs(dir_path, exist_ok=True)
            png_path = os.path.normpath(os.path.join(dir_path, file_name))
            json_path = png_path.replace(".png", ".json")

            logger.info(f"Iniciando captura de pantalla con Playwright para {symbol} ({timeframe}) - URL: {self.render_url}")
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1200, "height": 600})
                page = await context.new_page()
                
                # Listen to browser logs and exceptions
                page.on("console", lambda msg: logger.info(f"[BROWSER CONSOLE] {msg.text}"))
                page.on("pageerror", lambda err: logger.error(f"[BROWSER EXCEPTION] {err}"))
                
                # Carga del frontend con parámetros
                target_url = f"{self.render_url}?symbol={symbol}&timeframe={timeframe}"
                await page.goto(target_url, timeout=15000)
                
                # Esperamos al marcador de carga completa del gráfico
                try:
                    await page.wait_for_selector("#chart_loaded_signal", state="attached", timeout=10000)
                except Exception as wait_err:
                    logger.warning(f"No se detectó el marcador #chart_loaded_signal en 10s: {wait_err}. Continuando captura...")

                # Captura del elemento chart
                chart_element = page.locator("#chart")
                if await chart_element.count() > 0:
                    await chart_element.screenshot(path=png_path)
                else:
                    logger.warning("Elemento '#chart' no encontrado en el DOM. Capturando la página completa.")
                    await page.screenshot(path=png_path)
                
                await browser.close()

            # Calcular hash SHA256 para integridad
            with open(png_path, "rb") as f:
                image_bytes = f.read()
                image_hash = hashlib.sha256(image_bytes).hexdigest()

            # Crear metadatos detallados
            metadata = {
                "artifactId": artifact_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "traceId": trace_id,
                "imageSha256": image_hash,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "renderUrl": target_url
            }

            # Persistir metadatos JSON
            with open(json_path, "w", encoding="utf-8") as jf:
                json.dump(metadata, jf, indent=2, ensure_ascii=False)

            logger.info(f"Captura visual y JSON guardados con éxito: {png_path}")
            return {
                "artifact_id": artifact_id,
                "png_path": png_path,
                "json_path": json_path,
                "image_sha256": image_hash
            }

        except Exception as e:
            logger.error(f"Fallo crítico al capturar pantalla para {symbol}: {e}", exc_info=True)
            return None
