# Heuristic Rules for Adjustment Layer

These rules define how the **Pre-Match Probability ($P_{pre}$)** is modified by **Live Events ($E$)** to produce **Live Probability ($P_{live}$)**.

## 1. The Red Card Rule
**Trigger**: Red Card for Team A.
**Logic**:
- **Early (< 30')**: $P_{teamA} = P_{teamA} \times 0.40$
- **Mid (30'-70')**: $P_{teamA} = P_{teamA} \times 0.55$
- **Late (> 70')**: $P_{teamA} = P_{teamA} \times 0.70$
- *Reasoning*: Playing with 10 men is a massive disadvantage, scaling with time remaining.

## 2. The Dominance Modifier (xG)
**Trigger**: Team A is losing/drawing but dominating xG.
**Logic**:
- Condition: $Score_A \le Score_B$ AND $xG_A > xG_B + 1.0$.
- Action: **Boost** $P_{teamA}$ by 1.2x (up to max 0.85).
- *Reasoning*: "Unlucky" teams often revert to mean (score) eventually. Identifies strong Value Bets.

## 3. The Time Decay (Draw Weight)
**Trigger**: Match is Draw and $Time > 75'$.
**Logic**:
- $P_{draw\_live} = P_{draw\_pre} + ((Time - 75) \times 0.015)$
- *Reasoning*: As time runs out, the probability of the state changing decreases.

## 4. "Park the Bus" (Possession w/o Threat)
**Trigger**: Team A Possession > 70% but Shots on Target < 2 (after 60').
**Logic**:
- $P_{teamA} = P_{teamA} \times 0.9$
- *Reasoning*: Sterile possession often leads to counters or 0-0.
