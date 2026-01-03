# ELO Rating Integration

## Goal
Improve model accuracy by adding ELO ratings. ELO provides a long-term measure of team strength that persists across seasons, unlike "Last 5 Games" form.

## Approach
We will calculate ELO ratings from our full historical dataset (2010—Present). We will **not** scrape external ELOs because name matching is error-prone. Our own history is ground truth.

## Steps
1.  **Create `ml_project/elo_engine.py`**:
    *   Class `EloTracker`.
    *   Method `calculate_elo(home_rating, away_rating, goal_diff, result)`.
    *   Method `process_history(df)`: Iterates through the dataframe (must be sorted by date) and assigns `H_elo` and `A_elo` to each match *before* updating the ratings based on the result.
    *   Parameters: K-factor (e.g., 20 for major leagues), basic formula.

2.  **Update `feature_engineering.py`**:
    *   Import `EloTracker`.
    *   Call `tracker.process_history(df)` at the beginning of feature engineering.

3.  **Update `train_model.py`**:
    *   Add `H_elo`, `A_elo` to `common_features`.
    *   Retrain.

## Formula
Standard ELO with Goal Difference adjustment (optional but recommended for football).
`Rn = Ro + K * G * (W - We)`
*   `K`: Weight index (e.g., 20).
*   `G`: Goal difference index (1 if draw/1-goal win, 1.5 if 2-goal, etc. or `ln(|GD|+1)`).
*   `W`: Result (1 win, 0.5 draw, 0 loss).
*   `We`: Expected result `1 / (10^(-dr/400) + 1)`.

## Verification
*   Check if 1X2 Accuracy improves > 50%.
