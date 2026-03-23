# Siguientes Fases: Orion despues de la migracion n8n

## Estado base

La Fase 0 deja a Orion como borde de integracion para:

- consultas de evaluacion via `/api/v1/consult`
- proxy y relay hacia n8n
- facade compatible con `NotificationPort`
- health check compatible con Spring en `/actuator/health`

Con eso, `llm-trade-nx` puede dejar de hablar directo con n8n.

## Fase 1: Cerrar la migracion operativa

Objetivo: pasar de "contrato listo" a "operacion real estable".

Entregables:

- importar y validar en n8n los workflows que apuntan a Orion
- definir los webhooks reales usados por:
  - `trading-decision`
  - `trade-executed`
  - `trade-closed`
  - `trading-error`
  - `performance-metrics`
- decidir si `n8n` vive dentro de este repo o como servicio externo
- documentar variables `.env` por ambiente
- ejecutar smoke tests end-to-end:
  - `llm-trade-nx -> Orion -> n8n`
  - `n8n -> Orion -> llm-trade-nx`

Checklist:

- `docker-compose up` levanta Orion sin configuracion manual adicional
- `/api/n8n/n8n-status` devuelve `true` contra la instancia real
- los endpoints `/api/notifications/*` responden `202` y llegan al workflow esperado
- el workflow MCP sigue operativo desde n8n

## Fase 2: Contratos tipados y observabilidad

Objetivo: reducir payloads ambiguos y mejorar diagnostico.

Entregables:

- reemplazar `dict[str, Any]` en notificaciones por modelos Pydantic
- versionar contratos JSON relevantes
- agregar `request_id`, `correlation_id` y `source` a notificaciones
- exponer metricas basicas:
  - latencia por endpoint
  - tasa de error por webhook
  - disponibilidad de n8n
- agregar logging estructurado para flujos de integracion

Checklist:

- cada notificacion queda trazable de Java hasta n8n
- errores 5xx incluyen contexto suficiente para diagnostico
- los tests cubren contratos invalidos y timeouts

## Fase 3: Evaluacion estrategica real en Orion

Objetivo: mover inteligencia de evaluacion fuera del simple relay.

Entregables:

- consolidar el comite actual como modulo de evaluacion tecnica
- agregar evaluadores estrategicos:
  - contexto de mercado
  - sesgo de sesion
  - consistencia con historial reciente
- persistir decisiones y racionales de Orion
- agregar endpoint para consultar historial de veredictos

Checklist:

- Orion produce veredictos reproducibles con razonamiento auditable
- la decision final no depende de n8n para existir
- n8n queda como automatizacion y orquestacion, no como logica core

## Fase 4: Integracion hibrida con memoria y aprendizaje

Objetivo: usar Orion como servicio de evaluacion con contexto acumulado.

Entregables:

- memoria episodica por simbolo, sesion o estrategia
- almacenamiento de resultados reales post-trade
- feedback loop para recalibrar expertos
- reglas de recomendacion:
  - aprobar
  - rechazar
  - hold
  - reducir riesgo
  - pedir confirmacion adicional

Checklist:

- Orion puede usar resultado historico para ajustar confianza
- las decisiones nuevas aprovechan contexto previo
- existe separacion clara entre inferencia, memoria y automatizacion

## Fase 5: Hardening de produccion

Objetivo: dejar el servicio listo para operacion sostenida.

Entregables:

- retries con backoff para llamadas a n8n y upstream
- circuit breaker para dependencia caida
- colas o buffer para eventos fire-and-forget
- autenticacion entre servicios
- rate limiting para endpoints expuestos
- dashboards y alertas

Checklist:

- un fallo de n8n no degrada la ruta principal de trading
- la perdida de eventos se detecta y mide
- existe estrategia clara de recovery

## Decision tecnica recomendada

Para este repo, el mejor siguiente paso es Fase 1.

Motivo:

- el contrato HTTP ya existe
- los tests locales ya pasan
- lo que falta es cerrar operacion real, no mas codigo base

## Archivos a tocar en la Fase 1

- `README.md`
- `.env.example`
- `docker-compose.yml`
- `n8n/`
- `scripts/smoke_test_orion.ps1`

Opcional:

- agregar `docs/` si la documentacion de despliegue empieza a crecer
