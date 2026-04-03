import os
import httpx
from groq import AsyncGroq

# Instancia global de Groq
_groq_client = None


def _clean_env(name: str) -> str:
    return (os.environ.get(name) or "").strip()

def get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        api_key = _clean_env("ORION_GROQ_API_KEY") or _clean_env("GROQ_API_KEY")
        _groq_client = AsyncGroq(api_key=api_key)
    return _groq_client

async def fetch_rag_memory(symbol: str) -> str:
    """Consulta asíncrona a n8n para traer contexto Postgres de strategy_episodes."""
    webhook_url = _clean_env("ORION_VIGILANTE_RAG_WEBHOOK_URL")
    if not webhook_url:
        return "[⚠] Memoria RAG no configurada en las variables de entorno."
        
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(webhook_url, json={"symbol": symbol}, timeout=8.0)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("rag_context", str(data))
            else:
                return f"[⚠] RAG Fetch error: HTTP {resp.status_code}"
    except Exception as e:
        return f"[⚠] RAG Fetch failed: {str(e)}"
