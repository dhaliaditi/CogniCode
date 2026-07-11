#!/bin/bash
# CogniCode setup — run once inside the cognicode/ directory.
# Creates a venv, installs pinned deps, retrains models locally.

set -e
COGNICODE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$COGNICODE_DIR"

echo "============================================"
echo "  CogniCode Setup"
echo "  Working in: $COGNICODE_DIR"
echo "============================================"

# 1. Virtual environment
echo ""
echo "[1/3] Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q

# 2. Install dependencies
echo ""
echo "[2/3] Installing dependencies..."
pip install \
    "numpy==1.24.4" \
    "scikit-learn==1.3.2" \
    "joblib==1.3.2" \
    "pandas==2.0.3" \
    "flask==3.0.3" \
    "radon==6.0.1" \
    "pylint==3.2.7" \
    "prospector==1.10.3" \
    "pyyaml==6.0.1"

# 3. Retrain models using the bundled dataset
echo ""
echo "[3/3] Retraining models using local numpy/sklearn (this takes ~2 min)..."

python3 - << 'PYEOF'
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.svm import SVR
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
import joblib
from pathlib import Path

GOLDEN    = Path(__file__).parent / "models" / "merged_400_with_tool_metrics.csv" \
            if False else Path("models/merged_400_with_tool_metrics.csv")
MODEL_DIR = Path("models")

df = pd.read_csv(GOLDEN)

FEATURE_COLS = [c for c in [
    'Issue', 'ClassName', 'CyclomaticComplexity', 'ComplexityRank', 'MaintainabilityIndex',
    'PylintIssues_C', 'PylintIssues_R', 'PylintIssues_W', 'PylintIssues_E', 'PylintIssues_F',
    'Comments', 'Multi', 'Blanks', 'PublicMethodsCount', 'CommentRatio',
    'HalsteadEffort', 'HalsteadDifficulty', 'HalsteadVocabulary',
    'bugs', 'code_smells', 'cognitive_complexity',
    'duplicated_blocks', 'duplicated_lines', 'effort_to_reach_maintainability_rating_a',
    'reliability_rating', 'reliability_remediation_effort',
    'security_rating', 'security_remediation_effort',
    'statements', 'sqale_index', 'sqale_debt_ratio', 'comment_lines_density',
    'AvgCountLine', 'AvgCountLineBlank', 'AvgCountLineCode', 'AvgCountLineComment',
    'AvgCyclomatic', 'CountClassBase', 'CountClassCoupled', 'CountClassCoupledModified',
    'CountClassDerived', 'CountDeclExecutableUnit', 'CountDeclInstanceVariable',
    'CountDeclMethod', 'CountDeclMethodAll', 'Cyclomatic', 'MaxCyclomatic',
    'MaxInheritanceTree', 'MaxNesting', 'RatioCommentToCode', 'SumCyclomatic'
] if c in df.columns]

df['ClassName'] = df['ClassName'].fillna('').astype(str).apply(
    lambda x: len(x.split('|')) if x else 0)

X      = df[FEATURE_COLS].fillna(0)
y_c    = df['Complexity']
y_r    = (df['Readability'] + df['Understandability']) / 2 \
         if 'Understandability' in df.columns else df['Readability']
y_m    = df['Overall Maintainability']
y_f    = df['Do you think this code needs refactoring?'].astype(int)

print("  RF — Complexity ...")
m = RandomForestRegressor(n_estimators=200, random_state=42); m.fit(X, y_c)
joblib.dump(m, MODEL_DIR / "rf_complexity.pkl")

print("  SVM — Readability ...")
m = Pipeline([('sc', StandardScaler()), ('svm', SVR(kernel='rbf'))]); m.fit(X, y_r)
joblib.dump(m, MODEL_DIR / "svm_readability.pkl")

print("  SVM — Maintainability ...")
m = Pipeline([('sc', StandardScaler()), ('svm', SVR(kernel='rbf'))]); m.fit(X, y_m)
joblib.dump(m, MODEL_DIR / "svm_maintainability.pkl")

print("  RF — Refactoring ...")
m = RandomForestClassifier(n_estimators=200, random_state=42); m.fit(X, y_f)
joblib.dump(m, MODEL_DIR / "rf_refactoring.pkl")

joblib.dump(FEATURE_COLS, MODEL_DIR / "feature_cols.pkl")
print(f"  Done. numpy={np.__version__}, sklearn={__import__('sklearn').__version__}")
PYEOF

# Create run.sh
cat > run.sh << 'RUNEOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python3 app.py
RUNEOF
chmod +x run.sh

echo ""
echo "============================================"
echo "  Setup complete!"
echo ""
echo "  Start CogniCode:    ./run.sh"
echo "  SSH tunnel:         ssh -L 5051:localhost:5050 eln263@srlab05.usask.ca -N"
echo "  Browser:            http://localhost:5051"
echo "============================================"
