import sys, json
from pathlib import Path
sys.path.insert(0, str(Path.home() / '.claude' / 'hooks'))
from core.drift_detector import detect_drift, format_drift_summary
drift = detect_drift()
print(format_drift_summary(drift))
print('---JSON---')
print(json.dumps(drift, default=str))
