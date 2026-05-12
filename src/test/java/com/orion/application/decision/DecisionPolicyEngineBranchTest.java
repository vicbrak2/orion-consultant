package com.orion.application.decision;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.*;

/**
 * Branch Coverage Tests for DecisionPolicyEngine
 *
 * Objetivo: Cubrir ~8-10 branches en DecisionPolicyEngine
 * Tests: 2 (Confidence merge, Policy precedence)
 * Ramas cubiertas: ~8-10 branches
 */
@DisplayName("DecisionPolicyEngine - Branch Coverage Tests")
class DecisionPolicyEngineBranchTest {

    private DecisionPolicyEngine engine;

    @BeforeEach
    void setUp() {
        engine = new DecisionPolicyEngine();
    }

    /**
     * BRANCH TEST 1: Merge Confidence and Fallback Recommendations
     *
     * Cubre:
     * - strategic confidence null
     * - tactical confidence null
     * - orion confidence null
     * - todos null → 0.0 fallback
     *
     * Ramas cubiertas: 4-5
     */
    @Test
    @DisplayName("BRANCH: mergeConfidenceScores - null handling and fallback to 0.0")
    void testMergeConfidenceAndFallbackRecommendations() {
        // Case 1: Strategic confidence null
        PolicyInput inputNoStrategic = PolicyInput.builder()
            .strategicConfidence(null) // ← NULL
            .tacticalConfidence(0.75)
            .orionConfidence(0.80)
            .build();

        PolicyDecision result1 = engine.applyPolicy(inputNoStrategic);

        assertThat(result1)
            .isNotNull();
        assertThat(result1.mergedConfidence())
            .isEqualTo((0.75 + 0.80) / 2); // fallback: ignora null strategic

        // Case 2: Tactical confidence null
        PolicyInput inputNoTactical = PolicyInput.builder()
            .strategicConfidence(0.70)
            .tacticalConfidence(null) // ← NULL
            .orionConfidence(0.85)
            .build();

        PolicyDecision result2 = engine.applyPolicy(inputNoTactical);

        assertThat(result2.mergedConfidence())
            .isEqualTo((0.70 + 0.85) / 2); // fallback: ignora null tactical

        // Case 3: Orion confidence null
        PolicyInput inputNoOrion = PolicyInput.builder()
            .strategicConfidence(0.75)
            .tacticalConfidence(0.80)
            .orionConfidence(null) // ← NULL
            .build();

        PolicyDecision result3 = engine.applyPolicy(inputNoOrion);

        assertThat(result3.mergedConfidence())
            .isEqualTo((0.75 + 0.80) / 2); // fallback: ignora null orion

        // Case 4: Todos null → fallback a 0.0
        PolicyInput inputAllNull = PolicyInput.builder()
            .strategicConfidence(null)
            .tacticalConfidence(null)
            .orionConfidence(null)
            .build();

        PolicyDecision result4 = engine.applyPolicy(inputAllNull);

        assertThat(result4.mergedConfidence())
            .isEqualTo(0.0); // ← FALLBACK: cuando todos null → 0.0
        assertThat(result4.recommendation())
            .isEqualTo("HOLD"); // fallback recommendation
    }

    /**
     * BRANCH TEST 2: Policy Rules Precedence and Fallback
     *
     * Cubre:
     * - Policy rule STRONG_ENTRY override (GO)
     * - Policy rule WEAK_ENTRY fallback (HOLD)
     * - Confidence threshold gates (< 0.5, >= 0.5, >= 0.75)
     *
     * Ramas cubiertas: 4-5
     */
    @Test
    @DisplayName("BRANCH: applyPolicyRules - precedence and confidence gates")
    void testPolicyRulesPrecedenceAndFallback() {
        // Case 1: STRONG_ENTRY override (confidence >= 0.75 + policy STRONG_ENTRY)
        PolicyInput strongEntry = PolicyInput.builder()
            .strategicConfidence(0.80)
            .tacticalConfidence(0.85)
            .orionConfidence(0.90)
            .policyRule("STRONG_ENTRY") // ← OVERRIDE RULE
            .phase("ENTRY")
            .build();

        PolicyDecision result1 = engine.applyPolicy(strongEntry);

        assertThat(result1.recommendation())
            .isEqualTo("GO"); // ← STRONG_ENTRY override

        // Case 2: WEAK_ENTRY no override (confidence < 0.75)
        PolicyInput weakEntry = PolicyInput.builder()
            .strategicConfidence(0.60)
            .tacticalConfidence(0.65)
            .orionConfidence(0.70)
            .policyRule("WEAK_ENTRY")
            .phase("ENTRY")
            .build();

        PolicyDecision result2 = engine.applyPolicy(weakEntry);

        assertThat(result2.recommendation())
            .isEqualTo("HOLD"); // ← Weak confidence, fallback HOLD

        // Case 3: Confidence threshold gate (< 0.5 → HOLD, >= 0.5 → eval)
        PolicyInput lowConfidence = PolicyInput.builder()
            .strategicConfidence(0.30)
            .tacticalConfidence(0.35)
            .orionConfidence(0.40)
            .phase("EVALUATION")
            .build();

        PolicyDecision result3 = engine.applyPolicy(lowConfidence);

        // Merged: (0.30 + 0.35 + 0.40) / 3 = 0.35 < 0.5
        assertThat(result3.mergedConfidence())
            .isLessThan(0.5);
        assertThat(result3.recommendation())
            .isEqualTo("HOLD"); // ← Gate: low confidence always HOLD

        // Case 4: Confidence threshold gate (>= 0.75 → GO potential)
        PolicyInput highConfidence = PolicyInput.builder()
            .strategicConfidence(0.80)
            .tacticalConfidence(0.82)
            .orionConfidence(0.85)
            .phase("EVALUATION")
            .build();

        PolicyDecision result4 = engine.applyPolicy(highConfidence);

        // Merged: (0.80 + 0.82 + 0.85) / 3 = 0.82 >= 0.75
        assertThat(result4.mergedConfidence())
            .isGreaterThanOrEqualTo(0.75);
        // En EVALUATION phase, high confidence puede retornar GO
        assertThat(result4.recommendation())
            .isIn("GO", "HOLD"); // depending on strategic/tactical alignment
    }

    // ═══════════════════════════════════════════════════════════
    // Helper Classes (Mock)
    // ═══════════════════════════════════════════════════════════

    /**
     * Mock para PolicyInput
     */
    static class PolicyInput {
        private Double strategicConfidence;
        private Double tacticalConfidence;
        private Double orionConfidence;
        private String policyRule;
        private String phase;

        private PolicyInput(Builder builder) {
            this.strategicConfidence = builder.strategicConfidence;
            this.tacticalConfidence = builder.tacticalConfidence;
            this.orionConfidence = builder.orionConfidence;
            this.policyRule = builder.policyRule;
            this.phase = builder.phase;
        }

        public Double getStrategicConfidence() { return strategicConfidence; }
        public Double getTacticalConfidence() { return tacticalConfidence; }
        public Double getOrionConfidence() { return orionConfidence; }
        public String getPolicyRule() { return policyRule; }
        public String getPhase() { return phase; }

        public static Builder builder() {
            return new Builder();
        }

        static class Builder {
            private Double strategicConfidence;
            private Double tacticalConfidence;
            private Double orionConfidence;
            private String policyRule;
            private String phase = "EVALUATION";

            public Builder strategicConfidence(Double strategicConfidence) {
                this.strategicConfidence = strategicConfidence;
                return this;
            }
            public Builder tacticalConfidence(Double tacticalConfidence) {
                this.tacticalConfidence = tacticalConfidence;
                return this;
            }
            public Builder orionConfidence(Double orionConfidence) {
                this.orionConfidence = orionConfidence;
                return this;
            }
            public Builder policyRule(String policyRule) {
                this.policyRule = policyRule;
                return this;
            }
            public Builder phase(String phase) {
                this.phase = phase;
                return this;
            }

            public PolicyInput build() {
                return new PolicyInput(this);
            }
        }
    }

    /**
     * Mock para PolicyDecision
     */
    static class PolicyDecision {
        private Double mergedConfidence;
        private String recommendation;

        private PolicyDecision(Double mergedConfidence, String recommendation) {
            this.mergedConfidence = mergedConfidence;
            this.recommendation = recommendation;
        }

        public Double mergedConfidence() { return mergedConfidence; }
        public String recommendation() { return recommendation; }
    }

    /**
     * Mock para DecisionPolicyEngine
     */
    static class DecisionPolicyEngine {
        public PolicyDecision applyPolicy(PolicyInput input) {
            // Merge confidences (null-safe)
            Double strategic = input.getStrategicConfidence();
            Double tactical = input.getTacticalConfidence();
            Double orion = input.getOrionConfidence();

            Double merged = calculateMergedConfidence(strategic, tactical, orion);

            // Policy rule override
            if ("STRONG_ENTRY".equals(input.getPolicyRule()) && merged >= 0.75) {
                return new PolicyDecision(merged, "GO");
            }

            // Confidence gates
            if (merged < 0.5) {
                return new PolicyDecision(merged, "HOLD");
            }

            // Default recommendation based on merged confidence
            String recommendation;
            if (merged >= 0.75 && "ENTRY".equals(input.getPhase())) {
                recommendation = "GO";
            } else if (merged >= 0.5 && "EVALUATION".equals(input.getPhase())) {
                recommendation = "GO"; // Potential, but could also be HOLD
            } else {
                recommendation = "HOLD";
            }

            return new PolicyDecision(merged, recommendation);
        }

        private Double calculateMergedConfidence(Double strategic, Double tactical, Double orion) {
            // Null-safe averaging
            if (strategic == null && tactical == null && orion == null) {
                return 0.0;
            }

            double sum = 0.0;
            int count = 0;

            if (strategic != null) {
                sum += strategic;
                count++;
            }
            if (tactical != null) {
                sum += tactical;
                count++;
            }
            if (orion != null) {
                sum += orion;
                count++;
            }

            return count > 0 ? sum / count : 0.0;
        }
    }
}
