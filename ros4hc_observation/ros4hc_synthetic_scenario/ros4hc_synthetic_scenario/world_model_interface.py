"""将来のモデル差し替え契約。現在状態分類器をWorld Modelとは呼ばない。"""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from .trajectory import audit_model_input


@dataclass
class Prediction:
    # horizon_minutes -> 観測値/状態。提供しない出力はNone。
    predicted_observations: dict | None = None
    predicted_state: dict | None = None
    latent_state: object = None
    uncertainty: object = None
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class WorldModelAdapter(Protocol):
    def predict(self, model_input: dict, horizons_minutes: tuple[int, ...]) -> Prediction:
        ...


class PersistenceBaseline:
    """持続予測baseline。World Modelではない。学習なし。"""
    def predict(self, model_input, horizons_minutes):
        audit_model_input(model_input)
        current = model_input['history'][-1]
        return Prediction(predicted_observations={h: {
            'mean_hr_bpm': current['hr'].get('mean_hr_bpm'),
            'activity_magnitude_mean': current['acc'].get('activity_magnitude_mean')}
            for h in horizons_minutes}, metadata={'adapter': 'persistence_forecasting_baseline'})
