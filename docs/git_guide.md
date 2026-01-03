# Git Repository Initialization Guide

Follow this guide to initialize the project as a Git repository and push it to GitHub.

## 1. Initialize Local Repository
Run these commands in the project root (`/Users/thodorischaros/Documents/bet/flashscore-scraper`):

```bash
# Initialize git
git init

# Add all files (respecting .gitignore)
git add .

# Create first commit
git commit -m "Initial commit: Flashscore Predictor with Web UI, ML Pipeline, and Automation"
```

## 2. Create Repository on GitHub
1.  Go to [GitHub.com](https://github.com) and log in.
2.  Click the **+** icon in the top right and select **New repository**.
3.  **Repository name**: `flashscore-scraper` (or your preferred name).
4.  **Description**: "Soccer prediction engine with XGBoost, Heuristics, and Web Dashboard."
5.  **Visibility**: Public or Private.
6.  **Do NOT** check "Initialize this repository with a README" (we already created one).
7.  Click **Create repository**.

## 3. Link and Push
Replace `YOUR_USERNAME` with your actual GitHub username.

```bash
# Rename branch to main
git branch -M main

# Add remote origin
git remote add origin https://github.com/YOUR_USERNAME/flashscore-scraper.git

# Push to GitHub
git push -u origin main
```

## 4. Updates
When you make changes in the future:

```bash
git add .
git commit -m "Description of changes"
git push
```
