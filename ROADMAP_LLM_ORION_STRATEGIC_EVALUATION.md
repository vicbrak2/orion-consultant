# Roadmap: LLM + Orion para Evaluación Estratégica

## Objetivo General

Combinar `llm-trade-nx` (backend Java) y `orion-consultant` (comité Python) para soportar dos modos de evaluación:
- **`tactical`**: Decisiones rápidas, determinísticas y de bajo contexto. Orion opera solo. Útil para trades tipo sniper.
- **`strategic`**: Decisiones con lectura histórica del episodio. Usa LLM para la narrativa episódica + validación técnica estricta de Orion.

---

## Estado Actual de la Arquitectura

### 🌌 Orion (Microservicio Python)
- Actúa como servicio stateless para consultas técnicas HTTP.
- Mantiene a `risk_manager`, `trend_analyzer` y `pattern_expert`.
- Actúa como fachada/proxy hacia los webhooks de n8n para notificaciones.

### ☕ llm-trade-nx (Backend Java)
- Dueño absoluto del contexto histórico y recurrente (Postgres).
- Reconstruye episodios (`EpisodePacketBuilder`).
- Centraliza el policy engine híbrido (`HybridDecisionService`).
- Redirige notificaciones usando `OrionNotificationAdapter`.

---

## 📈 Fases del Roadmap

### ✅ Fase 0: Baseline y separación de modos
- [x] Configuración redirigida `n8n.enabled=false, orion.enabled=true`.
- [x] Clases n8n viejas eliminadas de `llm-trade-nx`.
- [x] Orion asume el rol de evaluación y enruta notificaciones de `NotificationPort` a n8n.

### ✅ Fase 1: Episodio mínimo persistente
- [x] Contexto `decision_context` enriquecido (`_episode`, `_market`, `_account`).
- [x] `trade_events` poblado correctamente con transiciones (`POSITION_OPENED`, `POSITION_CLOSED`).
- [x] Reconstrucción de episodio por `traceId`.

### ✅ Fase 2: Packet de contexto estratégico
- [x] `EpisodePacketBuilder` tipado y funcionando.
- [x] Normalización de estados (`PENDING`, `OPEN`, `CLOSED`).

### ✅ Fase 3: Servicio de evaluación estratégica
- [x] Creación de `StrategicEvaluationService` (read-only) del lado de Java usando LLM.

### ✅ Fase 4: Integración híbrida LLM + Orion
- [x] Orion implementado como input real a través de `OrionEvaluationPort` y `OrionEvaluationAdapter`.

### ✅ Fase 5: Policy engine de consolidación
- [x] Creación de `HybridDecisionService.java` para consolidar el veredicto conjunto (LLM + Orion).
- [x] Flags introducidos: `decision.mode` y `decision.hybrid.shadow-mode`.
- [x] Persistencia de las piezas: `_policy`, `_strategic` y `_orion` dentro de `decision_context`.

### ✅ Fase 6: Redis como cache caliente
- [x] Estrategia cache-first en `EpisodePacketBuilder`.
- [x] Invalidación real en `TradeLifecycleService` (apertura y cierre de trades).
- [x] Trazabilidad de métricas de hit/miss del cache.

### ✅ Fase 7: Entidad explícita de episodio (`strategy_episodes`)
- [x] Formalizada la memoria estratégica creando una tabla y entidad `strategy_episodes`.
- [x] Campos de atributos dispersos se han consolidado: mode, final_action, confidences, entry/exit prices, etc.
- [x] Se creó el `EpisodePacketBuilder` adaptado, combinando DB y el Hot Cache de Redis.
- [x] Se añadió `EpisodeQueryController.java` para consultas directas.

### ✅ Fase 8: Feedback loop y evaluación ex post
- [x] Agregadas columnas de calculo directo para outcomes (WIN/LOSS/BREAKEVEN), R:R, duration, etc.
- [x] Creado `EpisodeOutcomeService` que cruza la tesis de Orion/LLM contra el PNL final en la validación real de un trade cerrado (thesisAlignment).
- [x] Lógica de evaluación engatillada justo al final del `TradeLifecycleService`.

### ✅ Fase 9: Observabilidad y gobierno de IA
- [x] Instrumentación vía Micrometer directo en el policy-engine (`HybridDecisionService`).
- [x] Mapeo de latencias de invocaciones aisladas (`hybrid.orion.latency`, `hybrid.strategic.latency`).
- [x] Registro de fallbacks, overrides y divergences.
- [x] Se crearon dashboards y rulesetas de prometheus: `ai-governance-dashboard.json`, `ai-governance-alerts.yaml`.

---

## ⏳ Fases Pendientes (Próximos Pasos de Desarrollo)

### 🔴 Paso Cero Inmediato: Verificación Operativa / E2E (Script Smoke Test)
- [ ] Construir o aplicar `scripts/smoke_test_e2e.ps1`.
- [ ] Correr la infraestructura completa en red aislada (`docker-compose`).
- [ ] Simular full lifecycle llamando a `/api/v2/ea/decide-and-size` -> apertura -> cierre.
- [ ] Comprobar persistencia física en Redis y Postgres (strategy_episodes debe pasar PENDING->OPEN->CLOSED).
- [ ] Certificar que el sistema integrado está listo para operar sin contratiempos.

---

## 🧹 Tareas de Limpieza y Deuda Técnica Pendiente

- [ ] Mover los templates/workflows JSON de n8n al repositorio de `orion-consultant` para que el código y la tubería de automatización vivan juntos.
