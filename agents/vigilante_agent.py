import json
import agents.rag_client as rag_client
from models.schemas import ExpertOpinion, ExpertName, Verdict, VigilanteRequest

async def evaluate_vigilante_episode(request: VigilanteRequest) -> ExpertOpinion:
    """
    Evalua una operación abierta usando el LLM Llama3 en Groq.
    Inyecta como contexto el historial que trae n8n desde Postgres.
    """
    # 1. Fetch Contexto Histórico (RAG vía n8n)
    memory_context = await rag_client.fetch_rag_memory(request.symbol)
    
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
    
    client = rag_client.get_groq_client()
    
    try:
        response = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="llama-3.1-8b-instant", 
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
