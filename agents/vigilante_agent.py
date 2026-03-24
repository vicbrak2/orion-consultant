import os
import json
import httpx
from groq import AsyncGroq
from models.schemas import ExpertOpinion, ExpertName, Verdict, VigilanteRequest

# Instancia de Groq para consumo asincrono
# Requiere GROQ_API_KEY en variables de entorno
_groq_client = None

def get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY", ""))
    return _groq_client

async def fetch_rag_memory_from_n8n(symbol: str) -> str:
    """Consulta asíncrona a n8n para traer contexto Postgres de strategy_episodes."""
    webhook_url = os.environ.get("ORION_VIGILANTE_RAG_WEBHOOK_URL")
    if not webhook_url:
        return "[⚠] No RAG URL configured (ORION_VIGILANTE_RAG_WEBHOOK_URL is missing)."
        
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

async def evaluate_vigilante_episode(request: VigilanteRequest) -> ExpertOpinion:
    """
    Evalua una operación abierta usando el LLM Llama3 en Groq.
    Inyecta como contexto el historial que trae n8n desde Postgres.
    """
    # 1. Fetch Contexto Histórico (RAG vía n8n)
    memory_context = await fetch_rag_memory_from_n8n(request.symbol)
    
    # 2. Requisitos estrictos de salida del LLM
    system_prompt = f"""
    Eres el Vigilante AI del Orion Committee.
    Tu único objetivo es gestionar una posición abierta ({request.direction} en {request.symbol})
    y dictaminar una decisión:
    
    - "APPROVE": Sugiere mantenerla o incluso agregar posición (riesgo aceptable, a favor del trend).
    - "REJECT": Sugiere CIERRE INMEDIATO o salto de Stop Loss (volatilidad anómala en contra).
    - "HOLD": Mantener vigilancia y no hacer nada.
    
    === CONTEXTO HISTÓRICO RECIENTE (inyectado por n8n Postgres) ===
    {memory_context}
    ================================================================
    
    Tus reglas:
    1. Si el Win Rate histórico del contexto es pésimo y la posición actual va en negativo, dictamina REJECT (cierre).
    2. Si hay volatilidad extrema, asume postura hiper-defensiva.
    3. ERES UN JSON BOT. TU RESPUESTA DEBE SER ESTRICTAMENTE UN JSON. 
    Ejemplo: {{"decision": "HOLD", "confidence": 0.80, "reason": "Justificacion corta."}}
    No respondas código markdown ni otra cosa fuera del JSON puro.
    """
    
    user_prompt = f"""
    === ESTADO ACTUAL DEL EPISODIO ===
    Symbol: {request.symbol}
    Direction: {request.direction.value}
    Entry Price: {request.entry_price}
    Current Price: {request.current_price}
    Duration (minutes): {request.duration_minutes}
    Unrealized PNL: {request.unrealized_pnl}
    Volatility: {request.current_volatility}
    RSI: {request.rsi_value if request.rsi_value else "Unknown"}
    """
    
    client = get_groq_client()
    
    try:
        response = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="llama3-70b-8192", 
            response_format={"type": "json_object"},
            temperature=0.0
        )
        
        raw_json = response.choices[0].message.content
        parsed = json.loads(raw_json)
        
        # Parse decision
        decision_str = parsed.get("decision", "HOLD").upper()
        if decision_str == "APPROVE":
            verdict = Verdict.APPROVE
        elif decision_str == "REJECT":
            verdict = Verdict.REJECT
        else:
            verdict = Verdict.HOLD
            
        reason_llm = parsed.get("reason", "Sin razon reportada.")
        
        return ExpertOpinion(
            expert=ExpertName.VIGILANTE_AGENT,
            verdict=verdict,
            confidence=round(float(parsed.get("confidence", 0.5)), 2),
            reason=f"[GROQ RAG] {reason_llm}"
        )
        
    except Exception as e:
        return ExpertOpinion(
            expert=ExpertName.VIGILANTE_AGENT,
            verdict=Verdict.HOLD,
            confidence=0.5,
            reason=f"[Groq Error] Fallback a HOLD. Error: {str(e)}"
        )
