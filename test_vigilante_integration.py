import httpx
import asyncio
import json

async def run_test():
    url = "http://localhost:8100/api/v1/vigilante-evaluation"
    payload = {
        "symbol": "Step Index",
        "ticket_id": 999111,
        "direction": "SELL",
        "entry_price": 8045.0,
        "current_price": 8055.0,
        "current_volatility": 150.0,
        "unrealized_pnl": -15.5,
        "duration_minutes": 45,
        "rsi_value": 72.5
    }
    
    print(f"Enviando payload de test Vigilante a Orion ({url})...")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=20.0)
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print("\nRespuesta Cruda del Agente LLM Groq + RAG:")
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                print(f"Error HTTP: {response.text}")
                
    except Exception as e:
        print(f"Excepcion durante el test: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
