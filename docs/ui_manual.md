# Flashscore Predictor UI Manual

The Web Dashboard serves as the command center for running predictions, verifications, and managing data.

## 1. Dashboard Home
The main page lists all generated reports sorted by date.
*   **Predictions List**: Shows all generated `predictions_YYYY-MM-DD.csv` files.
    *   **Action**: Click "View" to see the detailed table of predictions for that day.
*   **Verification List**: Shows all `verification_YYYY-MM-DD.csv` files (results of comparing predictions vs actuals).
*   **Global Stats**: Displays overall accuracy for the loaded leagues at the top.

## 2. Navigation Bar (Top Menu)

### ⚽ Flashscore Predictor
Links back to the Home Dashboard.

### 💾 Data (Dropdown)
Contains administrative tasks for data management:
*   **Update Current Season (Results)**: Downloads the latest match results (CSV) from *Football-Data.co.uk*. This now updates **both** Main 22 Leagues and all configured **Extra Leagues** (USA, BRA, JPN, etc.). Use this daily.
*   **Update Standings & Form**: Scrapes Flashscore for the latest league tables and form guides. This updates the JSON files used by the *Heuristic Adjuster*.
*   **🔄 Retrain Model**: Runs the **Full Pipeline**:
    1.  Updates Results.
    2.  Updates Standings.
    3.  Retrains the XGBoost Model.
    *Use this once a week or when significant new data is available.*

### 💸 Betting Strategy & Simulator
Opens the **Betting Simulator Dashboard**, a fully functional virtual sportsbook manager.

#### **1. The Strategy (Automated)**
The system uses a sophisticated 3-layer approach to identify value bets:
*   **Layer 1: Probability**: The XGBoost ML Model predicts the probability of an outcome (1X2 or Over/Under).
*   **Layer 2: Expected Value (EV)**: It compares the Model's Probability against the Bookmaker's Odds (Bet365).
    *   *Formula*: `EV = (Model_Prob * Odds) - 1`
    *   *Rule*: Only bets with **Positive EV (> 0)** are selected.
*   **Layer 3: Stake Management (Kelly Criterion)**:
    *   The system calculates the optimal stake size using the **Kelly Criterion**.
    *   **Configuration**: We use **Quarter-Kelly (0.25x)**. This is a conservative approach to protect the bankroll while capitalizing on the edge.

#### **2. Virtual Betting Workflow**
The built-in ledger mimics a real bank account to track performance honestly.

1.  **Generate Slip**:
    *   Click "Generate High Confidence Auto-Wager Slip".
    *   **Custom Bankroll**: You can optionally enter a "Session Bankroll" (e.g., $100) to limit your exposure for that specific day. If left empty, it uses your full Account Balance.
2.  **Place Bets (Debit)**:
    *   Review the generated slip.
    *   Click **"Place Bets"**.
    *   **Immediate Deduction**: The total stake is *immediately deducted* from your Bankroll.
    *   *Example*: Bankroll $1000 - Bet $50 = New Balance $950.
3.  **Wait for Results**:
    *   Matches take place.
4.  **Verification & Settlement (Credit)**:
    *   Run the **"Verify"** process (via Dashboard) the next day.
    *   The system compares predictions with actual results.
    *   **Settlement**: For every **Winning Bet**, the system credits the **Total Return** (Stake + Profit) back to your account.
    *   *Example (Win @ 2.00)*: Balance $950 + Return $100 = New Balance $1050.
    *   *Example (Loss)*: Balance $950 + $0 = $950.

#### **3. History**
*   **Open**: Bets placed but not yet verified.
*   **Won/Lost**: Finalized bets with P/L recorded.
*   **Void**: Bets where the match was postponed or data data was missing (Stake is returned).

### ⚙️ Server (Dropdown)
*   **Restart Server**: Reloads the Flask application (useful after code changes).
*   **Stop Server**: Shuts down the web interface.

## 3. Prediction View
Clicking a prediction file opens the **Prediction Report**.
*   **Filters**: Filter by League, Confidence Level, or Prediction Type (1/X/2 or O/U).
*   **Table Columns**:
    *   **1X2 Prediction**: The predicted outcome (1 = Home, X = Draw, 2 = Away).
    *   **Conf**: Model confidence score (0.00 - 1.00).
    *   **Heuristic Logs**: Explains *why* a confidence was boosted (e.g., "Form Boost Home").

## 4. Verification View
Clicking "Run Verification" (or viewing a verification file) compares predictions against reality.
*   **Green Rows**: Correct Predictions.
*   **Red Rows**: Incorrect Predictions.
*   **Stats**: Shows accuracy (%) for that specific day for both 1X2 and Over/Under 2.5 markets.
