import pandas as pd
import glob
import os
from typing import List, Optional

class DataLoader:
    def __init__(self, history_dir: str):
        self.history_dir = history_dir
        self.required_columns = [
            'date', 'home_team', 'away_team', 'FTHG', 'FTAG', 'FTR',
            'B365H', 'B365D', 'B365A'
        ]

    def load_historical_data(self) -> pd.DataFrame:
        """
        Loads and concatenates all CSV files from the history directory.
        """
        all_files = glob.glob(os.path.join(self.history_dir, "*.csv"))
        df_list = []
        
        print(f"Found {len(all_files)} historical files.")

        for filename in all_files:
            try:
                # Read CSV
                df = pd.read_csv(filename)
                
                # Normalize Columns
                # Standard map: Date->date, HomeTeam->home_team, AwayTeam->away_team
                # "New" Format map: Home->home_team, Away->away_team, HG->FTHG, AG->FTAG, Res->FTR
                col_map = {
                    'Date': 'date', 
                    'HomeTeam': 'home_team', 'AwayTeam': 'away_team',
                    'FTHG': 'FTHG', 'FTAG': 'FTAG', 'FTR': 'FTR', 
                    'Div': 'league',
                    'League': 'league', # "New" format has 'League' instead of 'Div'
                    # "New" Format extensions
                    'Home': 'home_team', 'Away': 'away_team',
                    'HG': 'FTHG', 'AG': 'FTAG', 'Res': 'FTR'
                }
                # Rename if exists
                df = df.rename(columns=col_map)
                
                # Lowercase other standard columns if needed, but strict mapping is safer for required ones
                
                # Ensure date is datetime
                if 'date' in df.columns:
                    # Football-data often uses dd/mm/yy or dd/mm/yyyy
                    # We utilize dayfirst=True for efficiency if standardized, 
                    # but if formats mix, 'mixed' is safer though slower.
                    # The warning suggests specifying format or avoiding dayfirst=True if iso.
                    # Given football-data is consistently DD/MM/YY(YY), we keep dayfirst but suppress warning
                    # or better: we use format='mixed' if available (pd 2.0+) or just ignore errors.
                    
                    # Fix: Use format='mixed' to silence warning about mixed iso/dayfirst
                    try:
                        df['date'] = pd.to_datetime(df['date'], format='mixed', dayfirst=True, errors='coerce')
                    except:
                         # Fallback for older pandas versions
                        df['date'] = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')
                
                # Handling Missing Odds for Extra Leagues (Use Avg/Max as fallback for B365)
                # Extra leagues like DNK.csv have AvgCH/AvgCD/AvgCA or MaxCH...
                # We map them to B365H/D/A if B365 is missing.
                if 'B365H' not in df.columns and 'AvgCH' in df.columns:
                     df['B365H'] = df['AvgCH']
                     df['B365D'] = df['AvgCD']
                     df['B365A'] = df['AvgCA']
                elif 'B365H' not in df.columns and 'MaxCH' in df.columns:
                     df['B365H'] = df['MaxCH']
                     df['B365D'] = df['MaxCD']
                     df['B365A'] = df['MaxCA']
                
                # ENFORCE NUMERIC TYPES for Key Columns
                # This prevents 'object' type errors in XGBoost if CSV contains strings/empty values
                numeric_cols = ['B365H', 'B365D', 'B365A', 'FTHG', 'FTAG']
                for col in numeric_cols:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')

                # Check for required columns (warn if missing but don't crash)
                missing_cols = [c for c in self.required_columns if c not in df.columns]
                if missing_cols:
                    # Try fallback: maybe old file had 'Div' but new one has 'Div' etc.
                    # If we renamed, we should be good.
                    print(f"Warning: File {filename} is missing columns: {missing_cols}")
                    continue 

                df_list.append(df)
            except Exception as e:
                print(f"Error reading {filename}: {e}")

        if not df_list:
            raise ValueError("No valid historical data found.")

        combined_df = pd.concat(df_list, ignore_index=True)
        
        # Sort by date
        combined_df = combined_df.sort_values('date').reset_index(drop=True)
        
        return combined_df

    def get_team_names(self, df: pd.DataFrame) -> List[str]:
        """Returns unique team names from the historical dataset."""
        unique_teams = pd.concat([df['home_team'], df['away_team']]).unique()
        return sorted(unique_teams.tolist())

if __name__ == "__main__":
    # Test execution
    loader = DataLoader("data_sets/MatchHistory")
    try:
        df = loader.load_historical_data()
        print(f"Total Matches Loaded: {len(df)}")
        print(df.head())
    except Exception as e:
        print(f"Loader failed: {e}")
