# 🌌 Orion Consultant

**Comité de Expertos** para decisiones de trading en Step Index.  
Microservicio en Python + FastAPI + MCP SDK.

---

## 📋 Requisitos

- **Python 3.11+**
- **Docker** (opcional, para despliegue con contenedor)

---

## 🚀 Inicio Rápido

### 1. Clonar e instalar dependencias

```bash
cd orion-consultant
pip install -r requirements.txt
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus valores
```

### 3. Ejecutar el servidor FastAPI + MCP

```bash
uvicorn main:app --reload --port 8090
```

Abre [http://localhost:8090/docs](http://localhost:8090/docs) para ver la documentación Swagger.

> **MCP Server** se monta automáticamente en `/mcp` vía StreamableHTTP.

### 4. Ejecutar tests

```bash
python -m pytest tests/ -v
```

---

## 🐳 Docker

### Build y ejecución

```bash
# Crear la red compartida (si no existe)
docker network create llm-trade-network

# Levantar el servicio
docker-compose up -d --build
```

### Verificar

```bash
curl http://localhost:8090/health
```

---

## 🔐 Autenticación

Todos los endpoints (excepto `/health`, `/actuator/health`, `/metrics`, `/docs`) requieren el header:

```
X-API-Key: <valor de ORION_API_KEY>
```

Configura la variable en tu `.env` o en el `docker-compose.yml`:

```properties
ORION_API_KEY=your-secret-key-here
```

> Si `ORION_API_KEY` está vacío, la autenticación se deshabilita (solo para desarrollo local).  
> **Nunca dejar vacío en producción.** Rotar la key si la imagen fue construida sin `.dockerignore`.

---

## 📡 Endpoints REST

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET`  | `/health` | Health check |
| `GET`  | `/actuator/health` | Alias compatible con Spring Actuator |
| `POST` | `/api/v1/consult` | Consulta completa al Comité (3 expertos) |
| `POST` | `/api/v1/consult/risk_manager` | Consulta individual al Risk Manager |
| `POST` | `/api/v1/consult/trend_analyzer` | Consulta individual al Trend Analyzer |
| `POST` | `/api/v1/consult/pattern_expert` | Consulta individual al Pattern Expert |
| `GET`  | `/api/n8n/health` | Health check orientado a flujos n8n |
| `POST` | `/api/n8n/process-event` | Fachada n8n -> Orion -> Java/upstream |
| `POST` | `/api/n8n/trigger-workflow` | Dispara webhooks n8n a través de Orion |
| `GET`  | `/api/n8n/n8n-status` | Verifica conectividad de Orion hacia n8n |
| `POST` | `/api/agent/chat` | Proxy a webhook de agente n8n |
| `POST` | `/api/notifications/trading-decision` | Relay `NotificationPort.notifyTradingDecision()` -> n8n |
| `POST` | `/api/notifications/trade-executed` | Relay `NotificationPort.notifyTradeExecuted()` -> n8n |
| `POST` | `/api/notifications/trade-closed` | Relay `NotificationPort.notifyTradeClosed()` -> n8n |
| `POST` | `/api/notifications/trading-error` | Relay `NotificationPort.notifyTradingError()` -> n8n |
| `POST` | `/api/notifications/performance-metrics` | Relay `NotificationPort.sendPerformanceMetrics()` -> n8n |

---

## 📨 JSON de Intercambio (Java ↔ n8n ↔ Orion)

### Request (señal de trading)

```json
{
  "symbol": "Step Index",
  "direction": "BUY",
  "entry_price": 5432.10,
  "stop_loss": 5400.00,
  "take_profit": 5500.00,
  "equity": 1000.00,
  "balance": 1050.00,
  "current_volatility": 120.5,
  "trend_h1": "bullish",
  "trend_h4": "bullish"
}
```

### Response (veredicto del comité)

```json
{
  "final_verdict": "APPROVE",
  "approved_count": 3,
  "rejected_count": 0,
  "opinions": [
    {
      "expert": "risk_manager",
      "verdict": "APPROVE",
      "confidence": 0.85,
      "reason": "Cuenta saludable. Riesgo bajo control."
    },
    {
      "expert": "trend_analyzer",
      "verdict": "APPROVE",
      "confidence": 0.90,
      "reason": "H1 alineado (bullish). | H4 alineado (bullish). | Alineación completa multi-timeframe."
    },
    {
      "expert": "pattern_expert",
      "verdict": "APPROVE",
      "confidence": 0.75,
      "reason": "R:R aceptable (1.47:1)."
    }
  ],
  "summary": "3/3 expertos aprobaron. Operación autorizada.",
  "timestamp": "2026-03-11T23:00:00Z"
}
```

---

## 🔧 MCP Tools (para n8n MCP Client)

| Tool | Descripción |
|------|-------------|
| `validate_risk` | Evalúa drawdown, volatilidad y distancia de stop-loss |
| `analyze_trend` | Analiza alineación de tendencia H1/H4 |
| `detect_patterns` | Detecta patrones del Step Index y valida R:R |
| `consult_committee` | Ejecuta los 3 expertos y consolida el veredicto |

### Configuración del nodo MCP en n8n

```json
{
  "transport": "streamableHttp",
  "url": "http://orion-consultant:8090/mcp/"
}
```

---

## 🏗️ Arquitectura

```
orion-consultant/
├── agents/                     # Lógica de cada experto
│   ├── risk_manager.py         # 🛡️ Drawdown y volatilidad
│   ├── trend_analyzer.py       # 📈 Estructura H1/H4
│   └── pattern_expert.py       # 🔍 Patrones Step Index
├── models/
│   └── schemas.py              # Modelos Pydantic (contratos JSON)
├── tests/                      # 🧪 62 tests (pytest)
│   ├── conftest.py             # Fixtures compartidos
│   ├── test_risk_manager.py    # Tests Risk Manager (11)
│   ├── test_trend_analyzer.py  # Tests Trend Analyzer (19)
│   ├── test_pattern_expert.py  # Tests Pattern Expert (18)
│   └── test_api.py             # Tests API integration (12)
├── n8n/                        # 📋 n8n workflow templates
│   ├── workflow_trading_signal.json    # Flujo REST directo
│   └── workflow_mcp_ai_agent.json     # Flujo MCP + AI Agent
├── config.py                   # Configuración (env vars)
├── main.py                     # 🌐 FastAPI + MCP mount
├── mcp_server.py               # 🔌 MCP Server (tools)
├── Dockerfile                  # Multi-stage build
├── docker-compose.yml          # Despliegue con red compartida
└── requirements.txt
```

---

## 🔄 Integración con n8n

### Opción 1: REST directo (sin AI)

1. **Nodo Webhook** → Recibe JSON del Java Bot
2. **Nodo HTTP Request** → POST a `/api/v1/consult`
3. **Nodo If** → Evalúa `final_verdict`
4. **Nodo HTTP** → Devuelve resultado a Java

> 📄 Ver template: `n8n/workflow_trading_signal.json`

### Opción 2: MCP + AI Agent (con LLM)

1. **Nodo Webhook** → Recibe JSON del Java Bot
2. **Nodo AI Agent** → Usa las MCP Tools de Orion
3. **Nodo MCP Client** → `http://orion-consultant:8090/mcp/`
4. **AI decide** → Interpreta opiniones con LLM
5. **Nodo HTTP** → Devuelve veredicto enriquecido

> 📄 Ver template: `n8n/workflow_mcp_ai_agent.json`

---

## ☕ Integración real con Java

La integración Java viva ya no se documenta con un example dentro de este repo.
El consumidor real es `llm-trade-nx`, que habla con Orion vía HTTP usando sus
adapters propios.

### Contrato real de evaluación

`llm-trade-nx` consulta Orion por HTTP en:

```text
POST /api/v1/consult
GET  /actuator/health
```

`POST /api/v1/consult` recibe el payload normalizado del pipeline híbrido y
devuelve un veredicto de comité usado por `OrionEvaluationAdapter` del lado Java.

### Contrato real de notificaciones n8n -> Orion

Si `llm-trade-nx` conserva `NotificationPort`, puede delegar n8n a Orion
llamando estos endpoints:

```text
POST /api/notifications/trading-decision
POST /api/notifications/trade-executed
POST /api/notifications/trade-closed
POST /api/notifications/trading-error
POST /api/notifications/performance-metrics
GET  /actuator/health
```

Cada endpoint recibe el `payload` tal cual llega desde Java y Orion lo reenvía
al webhook n8n configurado con las variables:

```properties
ORION_N8N_TRADING_DECISION_WEBHOOK_PATH=/webhook/trading-decision
ORION_N8N_TRADE_EXECUTED_WEBHOOK_PATH=/webhook/trade-executed
ORION_N8N_TRADE_CLOSED_WEBHOOK_PATH=/webhook/trade-closed
ORION_N8N_TRADING_ERROR_WEBHOOK_PATH=/webhook/trading-error
ORION_N8N_PERFORMANCE_METRICS_WEBHOOK_PATH=/webhook/performance-metrics
ORION_NOTIFICATION_TIMEOUT_SECONDS=10
```
