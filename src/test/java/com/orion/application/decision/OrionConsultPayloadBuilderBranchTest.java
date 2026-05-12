package com.orion.application.decision;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.*;

import java.util.Map;
import java.util.Objects;

/**
 * Branch Coverage Tests for OrionConsultPayloadBuilder
 *
 * Objetivo: Cubrir ~15-20 branches en OrionConsultPayloadBuilder
 * Tests: 4 (Null checks, Fallbacks, Timeframe resolution, Confirmations)
 * Ramas cubiertas: ~20 branches
 */
@DisplayName("OrionConsultPayloadBuilder - Branch Coverage Tests")
class OrionConsultPayloadBuilderBranchTest {

    private OrionConsultPayloadBuilder payload;

    @BeforeEach
    void setUp() {
        payload = new OrionConsultPayloadBuilder();
    }

    /**
     * BRANCH TEST 1: Analysis Layer - Null Checks and Blank Labels
     *
     * Cubre:
     * - analysis map es null
     * - labels son blank
     * - labels son empty
     *
     * Ramas cubiertas: 3-4
     */
    @Test
    @DisplayName("BRANCH: resolveAnalysisLayerContext - analysis map null o labels blank")
    void testAnalysisLayerContextNullsAndBlanks() {
        // Case 1: analysis map es null
        DecisionRequest requestNoAnalysis = createDecisionRequest(builder ->
            builder.analysis(null) // ← NULL
        );

        OrionConsultRequest result1 = payload.buildOrionPayload(requestNoAnalysis);

        assertThat(result1)
            .isNotNull();
        assertThat(result1.analysisContext())
            .isNotNull()
            .extracting("trendTimeframe", "macroTimeframe", "microTimeframe")
            .allMatch(Objects::isNull, "Todos los timeframes deben ser null cuando analysis es null");

        // Case 2: analysis map con labels blank
        DecisionRequest requestBlankLabels = createDecisionRequest(builder ->
            builder.analysis(Map.of(
                "trend", "   ",  // blank
                "macro", "",     // empty
                "micro", null
            ))
        );

        OrionConsultRequest result2 = payload.buildOrionPayload(requestBlankLabels);

        assertThat(result2.analysisContext())
            .extracting("trendTimeframe", "macroTimeframe", "microTimeframe")
            .allMatch(Objects::isNull, "Blank/empty labels deben resolver a null");
    }

    /**
     * BRANCH TEST 2: Trend Derivation - Fallbacks (MA null, Price equals MA)
     *
     * Cubre:
     * - H1Data con MA nulo
     * - currentPrice == ma_50 (SIDEWAYS)
     *
     * Ramas cubiertas: 3-4
     */
    @Test
    @DisplayName("BRANCH: deriveSpecificTrend - H1Data MA null, price equals MA")
    void testTrendDerivationFallbacks() {
        // Case 1: H1Data con MA nulo
        H1Data h1WithNullMa = H1Data.builder()
            .currentPrice(100.0)
            .ma_50(null) // ← NULL
            .build();

        DecisionRequest requestNullMa = createDecisionRequest(builder ->
            builder
                .h1Data(h1WithNullMa)
                .analysis(Map.of("trend", "H1"))
        );

        OrionConsultRequest result1 = payload.buildOrionPayload(requestNullMa);

        // Cuando MA es null, fallback a snapshot path
        assertThat(result1.analysisContext())
            .isNotNull()
            .extracting("trendH1")
            .isNotNull();

        // Case 2: currentPrice == ma_50 (no hay dirección clara)
        H1Data h1WithEqualPrice = H1Data.builder()
            .currentPrice(100.0)
            .ma_50(100.0) // ← EQUAL
            .build();

        DecisionRequest requestEqualPrice = createDecisionRequest(builder ->
            builder
                .h1Data(h1WithEqualPrice)
                .analysis(Map.of("trend", "H1"))
        );

        OrionConsultRequest result2 = payload.buildOrionPayload(requestEqualPrice);

        // Cuando currentPrice == MA, retorna "SIDEWAYS"
        assertThat(result2.analysisContext().trendH1())
            .isEqualTo("SIDEWAYS");
    }

    /**
     * BRANCH TEST 3: Timeframe Resolution and Fallbacks
     *
     * Cubre:
     * - Timeframe key con formato PERIOD_xxx
     * - Fallback a request.atr14()
     * - Fallback a request.adx14()
     *
     * Ramas cubiertas: 4-5
     */
    @Test
    @DisplayName("BRANCH: findTimeframe con key PERIOD_xxx, fallbacks de ATR/ADX")
    void testTimeframeResolutionAndFallbacks() {
        // Case 1: Timeframe key con formato PERIOD_M15
        Map<String, TimeframeSnapshot> timeframesWithPeriodKey = Map.of(
            "PERIOD_M15", createTimeframeSnapshot("M15")
        );

        DecisionRequest requestPeriodKey = createDecisionRequest(builder ->
            builder.timeframes(timeframesWithPeriodKey)
        );

        OrionConsultRequest result1 = payload.buildOrionPayload(requestPeriodKey);

        // findTimeframe debe encontrar "PERIOD_M15" cuando busca "M15"
        assertThat(result1.macroContext())
            .isNotNull();
        assertThat(result1.macroContext().atrMacro())
            .isNotNull();

        // Case 2: Macro snapshot sin ATR → fallback a request.atr14()
        TimeframeSnapshot macroWithoutAtr = TimeframeSnapshot.builder()
            .period("M15")
            .atr14(null) // ← NO ATR EN SNAPSHOT
            .build();

        DecisionRequest requestFallbackAtr = createDecisionRequest(builder ->
            builder
                .atr14(25.0) // ← fallback al request level
                .adx14(30.0)
                .timeframes(Map.of("M15", macroWithoutAtr))
        );

        OrionConsultRequest result2 = payload.buildOrionPayload(requestFallbackAtr);

        // fallback: atrMacro = request.atr14()
        assertThat(result2.macroContext().atrMacro())
            .isEqualTo(25.0);
    }

    /**
     * BRANCH TEST 4: Confirmations - SAR null, CLV low, Structure null
     *
     * Cubre:
     * - FSM con sarAdxSignal null
     * - currentClv <= 0.5
     * - macroStructureOk null
     *
     * Ramas cubiertas: 4-5
     */
    @Test
    @DisplayName("BRANCH: buildConfirmations - SAR null, CLV bajo, structure null")
    void testConfirmationsNullsAndThresholds() {
        // Case 1: FSM con sarAdxSignal null
        Fsm fsmNullSar = Fsm.builder()
            .sarAdxSignal(null) // ← NULL
            .sarAdxBlocking(false)
            .currentClv(0.7)
            .macroStructureOk(true)
            .build();

        DecisionRequest requestNullSar = createDecisionRequest(builder ->
            builder.fsm(fsmNullSar)
        );

        OrionConsultRequest result1 = payload.buildOrionPayload(requestNullSar);

        // sarAdxSignal null → sarAdxConfirmed = false
        assertThat(result1.confirmations().sarAdxConfirmed())
            .isFalse();

        // Case 2: FSM con CLV muy bajo
        Fsm fsmLowClv = Fsm.builder()
            .sarAdxSignal(1)
            .sarAdxBlocking(false)
            .currentClv(0.3) // ← LOW
            .macroStructureOk(true)
            .build();

        DecisionRequest requestLowClv = createDecisionRequest(builder ->
            builder.fsm(fsmLowClv)
        );

        OrionConsultRequest result2 = payload.buildOrionPayload(requestLowClv);

        // CLV <= 0.5 → clvConfirmed = false
        assertThat(result2.confirmations().clvConfirmed())
            .isFalse();

        // Case 3: macroStructureOk null
        Fsm fsmNullStructure = Fsm.builder()
            .sarAdxSignal(1)
            .sarAdxBlocking(false)
            .currentClv(0.7)
            .macroStructureOk(null) // ← NULL
            .build();

        DecisionRequest requestNullStructure = createDecisionRequest(builder ->
            builder.fsm(fsmNullStructure)
        );

        OrionConsultRequest result3 = payload.buildOrionPayload(requestNullStructure);

        // macroStructureOk null → macro_structure_confirmed = false
        assertThat(result3.confirmations().macroStructureConfirmed())
            .isFalse();
    }

    // ═══════════════════════════════════════════════════════════
    // Helper Methods
    // ═══════════════════════════════════════════════════════════

    private DecisionRequest createDecisionRequest(java.util.function.Consumer<DecisionRequest.DecisionRequestBuilder> customizer) {
        DecisionRequest.DecisionRequestBuilder builder = DecisionRequest.builder()
            .h1Data(H1Data.builder()
                .currentPrice(100.0)
                .ma_50(105.0)
                .build())
            .fsm(Fsm.builder()
                .sarAdxSignal(1)
                .sarAdxBlocking(false)
                .currentClv(0.7)
                .macroStructureOk(true)
                .build())
            .timeframes(Map.of())
            .analysis(Map.of());

        customizer.accept(builder);
        return builder.build();
    }

    private TimeframeSnapshot createTimeframeSnapshot(String period) {
        return TimeframeSnapshot.builder()
            .period(period)
            .atr14(20.0)
            .adx(25.0)
            .build();
    }
}
